import json
import multiprocessing
import os
import time
import sys
import struct
import re
import socket
import datetime
import traceback
import struct
from struct import calcsize, pack
from mrt2bmp.HelperClasses import MessageBucket, moveFileToTempDirectory, BMP_Helper, BGP_Helper
from mrt2bmp.MrtParser import MrtParser
from mrt2bmp.CollectorSender import BMPWriter
from mrt2bmp.logger import init_mp_logger

MRT_TYPES = {
    11: 'OSPFv2',
    12: 'TABLE_DUMP',
    13: 'TABLE_DUMP_V2',
    16: 'BGP4MP',
    17: 'BGP4MP_ET',
    32: 'ISIS',
    33: 'ISIS_ET',
    48: 'OSPFv3',
    49: 'OSPFv3_ET'
}

TABLE_DUMP_V2_SUBTYPES = {
    1: 'PEER_INDEX_TABLE',
    2: 'RIB_IPV4_UNICAST',
    3: 'RIB_IPV4_MULTICAST',
    4: 'RIB_IPV6_UNICAST',
    5: 'RIB_IPV6_MULTICAST',
    6: 'RIB_GENERIC',
}

class RibProcessor():

    def __init__(self, file_path, directory_path, router_name, collector_id, forward_queue, log_queue):
        self._file_path = file_path
        self._directory_path = directory_path
        self._router_name = router_name
        self._collector_id = collector_id
        self._forward_queue = forward_queue
        self._log_queue = log_queue

        self.working_dir = os.path.join(self._directory_path, self._router_name)
        if os.path.exists(os.path.join(self._directory_path, self._router_name, 'bgpdata')):
            self.working_dir = os.path.join(self._directory_path, self._router_name, 'bgpdata')

        # Peer index table is array of dictionaries.
        self._peer_index_table = []
        self.__setPeerIndexTable()
        self.__savePeerIndexTable()

        # Dictionary of MessageBucket objects.
        self.message_bucket_dict = {}



    # Main process function to be called.
    def processRibFile(self):

        # Iterate through update file.
        mp = MrtParser(os.path.join(self.working_dir, self._file_path))

        for m in mp:

            if MRT_TYPES[m['mrt_header']['type']] == 'TABLE_DUMP_V2' and \
                    (TABLE_DUMP_V2_SUBTYPES[m['mrt_header']['subtype']] == 'RIB_IPV4_UNICAST'):

                raw_prefix_nlri = m['mrt_entry']['raw_prefix_nlri']

                for e in m['mrt_entry']['rib_entries']:

                    # Key consists of peer index and hash of raw path attributes.
                    raw_path_attributes = b"".join(e['raw_bgp_attributes'])
                    bucket_key = str(e['peer_index']) + str(hash(raw_path_attributes))

                    if self.message_bucket_dict.has_key(bucket_key):

                        # Add the prefix to the corresponding MessageBucket.
                        self.message_bucket_dict[bucket_key].addPrefix(raw_prefix_nlri)

                    else:
                        try:
                            mp_reach_attribute = e['raw_mp_reach_nlri']['value']
                            # Create message bucket for the key.
                            peer = self._peer_index_table[e['peer_index']]

                            self.message_bucket_dict[bucket_key] = MessageBucket(peer, raw_path_attributes,
                                                                                 raw_prefix_nlri, mp_reach_attribute, self._forward_queue)

                        except KeyError as ex:
                            print("traceback caught when reading ribFile: %r (%r)" % (ex, e))
                        except:
                            pass

        # Send existing bucket messages.
        for k in self.message_bucket_dict:
            self.message_bucket_dict[k].finalizeBucket()

    # Peer Index Table functions.
    def __setPeerIndexTable(self):

        mp = MrtParser(os.path.join(os.path.join(self.working_dir, self._file_path)))

        for m in mp:

            if m['mrt_header']['type'] == 13 and m['mrt_header']['subtype'] == 1:

                # Create list of dicts.
                peer_list = m['mrt_entry']['peer_list']

                self._peer_index_table = peer_list

                break

    def __savePeerIndexTable(self):

        # Save peer index table as json
        path = os.path.join(self.working_dir, 'router_pit.json')

        # Init json object to be saved in a file.
        jsonToSave = {}
        jsonToSave['router_name'] = self._router_name
        jsonToSave['collector_id'] = self._collector_id
        jsonToSave['peer_list'] = self._peer_index_table
        jsonToSave['peer_index_table_source'] = self._file_path

        self.__generateChangedPeerMessages(self._peer_index_table)

        with open(path, 'w') as fp:
            json.dump(jsonToSave, fp, sort_keys=True, indent=4)

    def __generateChangedPeerMessages(self, new_peer_list):

        old_peer_list = []

        # If router_pit.json exists, then load peer index table.
        path = os.path.join(self.working_dir, 'router_pit.json')

        if os.path.isfile(path):
            with open(path) as data_file:
                old_peer_list = json.load(data_file)['peer_list']

        else:
            old_peer_list = new_peer_list

        # Find difference between old and new peers.
        new_peers = [x for x in new_peer_list if x not in old_peer_list]
        old_peers = [x for x in old_peer_list if x not in new_peer_list]

        # Create and send peer up message for each new peer.
        for peer in new_peers:

            if peer['bgp_id'] != "0.0.0.0":

                qm = BMP_Helper.createPeerUpMessage(peer, self._collector_id)

            else:

                qm = BMP_Helper.createPeerDownMessage(peer)

            self._forward_queue.put(qm)

        # Create and send peer down message for each old peer.
        for peer in old_peers:

            qm = BMP_Helper.createPeerDownMessage(peer)
            self._forward_queue.put(qm)


class UpdateProcessor():

    def __init__(self, file_path, directory_path, router_name, collector_id, forward_queue, log_queue):
        self._isProcessable = True
        self._peer_index_table = None
        self._file_path = file_path
        self._directory_path = directory_path
        self._router_name = router_name
        self._collector_id = collector_id
        self._forward_queue = forward_queue
        self._log_queue = log_queue
        self._peer_index_table= None
        self.LOG = init_mp_logger("updates_processor", self._log_queue)

        self.working_dir = os.path.join(self._directory_path, self._router_name)
        if os.path.exists(os.path.join(self._directory_path, self._router_name, 'bgpdata')):
            self.working_dir = os.path.join(self._directory_path, self._router_name, 'bgpdata')

        # Load peer index table from router_pit.json in router directory.
        self.__loadPeerIndexTable()

    def __searchInPeerIndexTable(self, peer_ip):

        peer = None

        for e in self._peer_index_table['peer_list']:

            if e['ip_address'] == peer_ip:
                peer = e
                break

        return peer

    def processUpdateFile(self):

        if self._isProcessable:

            # Iterate through update file.
            mp = MrtParser(os.path.join(self.working_dir, self._file_path))

            for m in mp:

                time_stamp_seconds = m['mrt_header']['timestamp']

                # Lookup peer in peer index table for peer bgp id
                if m['mrt_header']['type'] == 16 and (m['mrt_header']['subtype'] == 1 or m['mrt_header']['subtype'] == 4):

                    try:
                        peer = self.__searchInPeerIndexTable(m['mrt_entry']['peer_ip'])

                        if peer is not None:

                            if m['mrt_header']['subtype'] == 1:
                                peer['as_number_size'] = 2

                            # Encode BMP ROUTE-MONITOR message using BMP common header + per peer header + BGP message
                            raw_bgp_message = m['mrt_entry']['raw_bgp_message']

                            per_peer_header = BMP_Helper.createBmpPerPeerHeader(0, 0, peer, time_stamp_seconds, 0)

                            common_header = BMP_Helper.createBmpCommonHeader(3, len(per_peer_header) + len(raw_bgp_message) + 6, 0)

                            # Put the message in the queue.
                            qm = common_header + per_peer_header + raw_bgp_message

                            self._forward_queue.put(qm)

                    except KeyError as e:
                        self.LOG.warn("traceback caught when reading update: %r mh=(%r) mp=(%r)" % (e,
                                                                                                    m['mrt_header'],
                                                                                                    m['mrt_entry']))
                    except:
                        self.LOG.warn("traceback caught when reading update: %r" % sys.exc_info()[0])

                else:
                    self.LOG.info("Ignoring unsupported update type: %d subtype: %d" %(m['mrt_header']['type'], m['mrt_header']['subtype']))

    def __loadPeerIndexTable(self):

        # If router_pit.json exists, then load peer index table.
        path = os.path.join(self.working_dir, 'router_pit.json')

        if os.path.isfile(path):
            with open(path) as data_file:
                self._peer_index_table = json.load(data_file)

        # Else, does not process the update file because there is no peer index table.
        else:
            self._isProcessable = False
            self.LOG.error("There is no peer index table for the update file: %s/%s" % (self._router_name,
                                                                                        self._file_path))


class RouterProcessor:

    def __init__(self, router_name, directory_path, forward_queue, log_queue, cfg):

        # Regex for directory pattern to look for (YYYY-MM)
        self._date_dir_regex = re.compile('[0-9]{4,4}\.[0-9]{2,2}')

        self._cfg = cfg
        self._isToProcess = True
        self._router_name = router_name
        self._directory_path = directory_path
        self._processed_directory_path = cfg['processed_directory_path']

        self._fwd_queue = forward_queue

        self._log_queue = log_queue
        self.LOG = init_mp_logger("router_processor", self._log_queue)

        self._collector_id = None
        self._listOfRibAndUpdateFiles = []

        self.working_dir = os.path.join(self._directory_path, self._router_name)
        if os.path.exists(os.path.join(self._directory_path, self._router_name, 'bgpdata')):
            self.working_dir = os.path.join(self._directory_path, self._router_name, 'bgpdata')

        self.__collectListOfRibandUpdateFiles()
        self.__readCollectorId()


    def isToProcess(self):
        return self._isToProcess

    def __collectListOfRibandUpdateFiles(self):
        self.LOG.debug("Using working dir %s" % self.working_dir)

        for d in os.listdir(self.working_dir):

            if not self._date_dir_regex.match(d):
                # self.LOG.info("Skipping over directory %s" % d)
                continue

            if os.path.isdir(os.path.join(self.working_dir, d)):
                listOfRibs = []
                listOfUpdates = []

                rib_path = os.path.join(self.working_dir, d, "RIBS")
                update_path = os.path.join(self.working_dir, d, "UPDATES")

                if os.path.isdir(rib_path):
                    listOfRibs = os.listdir(rib_path)
                    listOfRibs = [os.path.join(d,"RIBS",e) for e in listOfRibs]

                if os.path.isdir(update_path):
                    listOfUpdates = os.listdir(update_path)
                    listOfUpdates = [os.path.join(d, "UPDATES", e) for e in listOfUpdates]

                self._listOfRibAndUpdateFiles = self._listOfRibAndUpdateFiles + listOfRibs + listOfUpdates

        # Create tuple list from list of ribs and updates.
        sorting_list = []

        for i, f in enumerate(self._listOfRibAndUpdateFiles):
            if "rib" in f or "bview" in f or "updates" in f:
                # Parse date of the file.
                tokens = f.split('.')
                try:
                    date = tokens[2] + tokens[3]
                    print(date[16:18])
                    date = datetime.datetime(int(date[0:4]), int(date[5:7]), int(date[8:10]), int(date[10:12]), int(date[13:15]), int(date[16:18]))
                    sorting_list.append((date, f))
                except:
                    self.LOG.warn("%s is not correctly formatted, skipping" % f)

        # Sorts files by their timestamp.
        def getDate(fileInfo):
            return fileInfo[0]

        sorting_list.sort(key=getDate)

        # If there is no file to process, then exit and do not run process function.
        if len(sorting_list) == 0:
            self._isToProcess = False
            #self.LOG.error("No RIBs and UPDATEs in %s" % os.path.join(self._directory_path, self._router_name))

        else:
            prevFileTimestamp = sorting_list[0][0]

            # Checks if there is an abnormality between mrt file timestamps.
            for f in sorting_list:
                currentFileTimestamp = f[0]
                timeDif = currentFileTimestamp - prevFileTimestamp

                if timeDif.total_seconds()/60 <= self._cfg['timestamp_interval_limit']:
                    pass

                else:
                    # There is an abnormality between timestamps of two files.
                    self._isToProcess = self._cfg['ignore_timestamp_interval_abnormality']
                    self.LOG.info("There is an abnormality between timestamps of two files in %s ." % self._router_name)

                prevFileTimestamp = currentFileTimestamp

            self._listOfRibAndUpdateFiles = []

            self._listOfRibAndUpdateFiles.append(sorting_list[0])
            i = 1
            while i < len(sorting_list):

                if sorting_list[i][1].find('rib') != -1:
                    break

                self._listOfRibAndUpdateFiles.append(sorting_list[i])
                i += 1

    def __readCollectorId(self):

        if self._isToProcess:

            # If router_pit.json exists, then read collector id from that file.
            path = os.path.join(self.working_dir, 'router_pit.json')

            if os.path.isfile(path):
                with open(path) as data_file:
                    data = json.load(data_file)
                    self._collector_id = data['collector_id']

            else:
                # If not, then read collector_id from the first rib file.
                firstRIB = self._listOfRibAndUpdateFiles[0][1]

                mp = MrtParser(os.path.join(self.working_dir, firstRIB))
                for m in mp:

                    if m['mrt_header']['type'] == 13 and m['mrt_header']['subtype'] == 1:
                        self._collector_id = m['mrt_entry']['collector_id']
                        break

    def getPeerMessages(self):

        peer_list = []

        # If router_pit.json exists, then read collector id from that file.
        path = os.path.join(self.working_dir, 'router_pit.json')

        first_file = self._listOfRibAndUpdateFiles[0][1]
        if first_file.find("rib.") != -1 or first_file.find("bview.") != -1:
            # If not, then read collector_id from the first rib file.
            mp = MrtParser(os.path.join(self.working_dir, first_file))
            for m in mp:

                if m['mrt_header']['type'] == 13 and m['mrt_header']['subtype'] == 1:
                    peer_list = m['mrt_entry']['peer_list']
                    break

        elif os.path.isfile(path):
            with open(path) as data_file:
                data = json.load(data_file)
                peer_list = data['peer_list']

        return_list = []

        for peer in peer_list:

            if peer["bgp_id"] != "0.0.0.0":

                qm = BMP_Helper.createPeerUpMessage(peer, self._collector_id)

                return_list.append(qm)

            else:

                qm = BMP_Helper.createPeerDownMessage(peer)

                return_list.append(qm)

        return return_list

    def getInitMessage(self):

        # Generate information TLVs about monitored router.
        """
         TLV Structure:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |          Information Type     |       Information Length      |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                 Information (variable)                        |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

         Type = 0: String
         Type = 1: sysDescr
         Type = 2: sysName

         sysDescr and sysName are must to sent.

        """

        # sysDescr tlv creation
        f1 = '!H H 1s'
        s1 = calcsize(f1)
        sysDescr_data = pack(f1, 1, 1, ' ')

        # sysName tlv creation
        f2 = '!H H ' + str(len(self._router_name)) + 's'
        s2 = calcsize(f2)
        sysName_data = pack(f2, 2, len(self._router_name), self._router_name)

        common_header = BMP_Helper.createBmpCommonHeader(3, s1 + s2 + 6, 4)

        qm = common_header + sysDescr_data + sysName_data

        return qm

    def getTerminationMessage(self):

        # Generate information TLVs about monitored router.
        """
         TLV Structure:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |          Information Type     |       Information Length      |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                 Information (variable)                        |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

         Type = 0: String
         Type = 1: reason

        """

        # Creation of Type 1 reason tlv
        information = struct.pack("!H", 0)
        reason_tlv = struct.pack("!H H", 1, len(information)) + information

        common_header = BMP_Helper.createBmpCommonHeader(3, len(reason_tlv) + 6, 5)

        qm = common_header + reason_tlv

        return qm

    def processRouteView(self, is_first_run):

        if self._isToProcess:

            for f in self._listOfRibAndUpdateFiles:

                self.LOG.info("-- %s is started" % f[1])

                if "rib" in f[1] or "bview" in f[1]:

                    rp = RibProcessor(f[1], self._directory_path, self._router_name, self._collector_id, self._fwd_queue, self._log_queue)

                    if is_first_run:
                        rp.processRibFile()

                    moveFileToTempDirectory(os.path.join(self.working_dir, f[1]),
                                            os.path.join(self._processed_directory_path, self._router_name, "RIBS"))

                elif "updates" in f[1]:

                    try:
                        up = UpdateProcessor(f[1], self._directory_path, self._router_name, self._collector_id, self._fwd_queue, self._log_queue)
                        up.processUpdateFile()
                    except:
                        traceback.print_exc();

                    moveFileToTempDirectory(os.path.join(self.working_dir, f[1]),
                                            os.path.join(self._processed_directory_path, self._router_name, "UPDATES"))

                self.LOG.info("-- %s is ended" % f[1])

        else:
            self.LOG.error("Data of %s cannot be processed..." % self._router_name)


class RouteViewsProcessor(multiprocessing.Process):

    def __init__(self, router_name, cfg, log_queue, fwd_queue, sync_mutex):
        """ Constructor

            :param cfg:             Configuration dictionary
            :param forward_queue:   Output for BMP raw message forwarding
            :param log_queue:       Logging queue - sync logging
        """

        multiprocessing.Process.__init__(self)
        self._stop = multiprocessing.Event()

        self.cfg = cfg
        self._cfg_router = cfg['router_data']
        self._cfg_collector = cfg['collector']
        self._log_queue = log_queue
        self._dir_path = self._cfg_router['master_directory_path']
        self.router_name = router_name
        self.LOG = None
        self._sync_mutex = sync_mutex

        # Start the BMP writer process
        self._fwd_queue = fwd_queue
        self._collector_writer = None

    def run(self):
        """ Override """
        self.LOG = init_mp_logger("mrt_processors", self._log_queue)

        # Parse name of router from router directory name.
        router_path = os.path.join(self._dir_path, self.router_name)
        if not os.path.isdir(router_path):
            self.LOG.error("%s is not a directory !" % router_path)
            sys.exit(2)

        try:
            self.LOG.info("- %s is started" % str(self.router_name))

            is_first_run = True

            while not self.stopped():

                self._sync_mutex.acquire()

                # Process the router by creating a 'RouterProcessor'
                rp = RouterProcessor(str(self.router_name), self._dir_path, self._fwd_queue, self._log_queue, self._cfg_router)

                if self._collector_writer is None and rp.isToProcess():
                    self._collector_writer = BMPWriter(self.cfg, self._fwd_queue, self._log_queue)
                    self._collector_writer.setInitialMessages(rp.getInitMessage(), rp.getPeerMessages(),
                                                              rp.getTerminationMessage())

                    self._collector_writer.start()

                if self._collector_writer is not None and rp.isToProcess():
                    self._collector_writer.setInitialMessages(rp.getInitMessage(), rp.getPeerMessages(), rp.getTerminationMessage())

                    rp.processRouteView(is_first_run)

                    is_first_run = False

                self._sync_mutex.release()

                # Waits for 30 seconds and runs the route views processor again.
                time.sleep(30)

        except KeyboardInterrupt:
            self.LOG.info("- %s is ended" % str(self.router_name))

        except:
            print (sys.exc_info()[0])
            traceback.print_exc()

    def stop(self):
        if self._collector_writer is not None:
            self._collector_writer.stop()

        self._stop.set()

    def stopped(self):
        return self._stop.is_set()