from urllib.request import urlopen
import os
import time
import datetime
import sys
import shutil
import json
import errno
import socket
from threading import Thread, Lock
from multiprocessing import Manager, Process
from mrt2bmp.logger import init_mp_logger

NAME_OF_DOWNLOAD_TRACK_FILE = "download_track.json"

class Downloader_Thread(Thread):

    def __init__(self, download_queue, master_dir, router_name, log):

        Thread.__init__(self)

        self.route_view_dir_path = master_dir
        self.download_queue = download_queue
        self.router_name = router_name
        self.current_timestamp = None
        self.LOG = log

    def run(self):

        while True:

            try:

                self.current_timestamp = datetime.datetime.utcnow()

                df = self.download_queue.get()

                path_parts = df[0].split("/")

                path = os.path.join(self.route_view_dir_path, df[1], path_parts[-3], path_parts[-2], path_parts[-1])
                key = os.path.join(path_parts[-3], path_parts[-2], path_parts[-1])

                # Create temporary path to download the mrt file.
                temp_file_name = 'download-mrt'
                temp_path = os.path.join(self.route_view_dir_path, df[1], path_parts[-3], path_parts[-2], temp_file_name)

                self.downloadHTTPFile(temp_path, df, key, path)

                self.download_queue.task_done()

            except EOFError as e:
                print (e)
                break

    def downloadHTTPFile(self, temp_path, df, key, path):

        url = df[0]

        while True:

            try:
                # Check if there is existing .download file, then delete it.
                if os.path.isfile(temp_path):
                    os.remove(temp_path)

                # Get file size and last modification date.
                url_handle = urlopen(url, None, 5.0)
                headers = url_handle.info()

                etag = headers.getheader("ETag")[1:-1]
                last_modified = headers.getheader("Last-Modified")
                file_size = int(headers.getheader("content-length"))

                time_difference_minutes = (self.current_timestamp - datetime.datetime.strptime(last_modified, '%a, %d %b %Y %H:%M:%S GMT')).total_seconds() / 60

                if not self.__checkFileMetadataInDownloadTrack(df[1], key, etag,
                                                               last_modified) and time_difference_minutes >= 3 and file_size > 14:
                    # Download the file.
                    chunk_size = 1024 * 256

                    f_output = open(temp_path, 'wb')

                    while True:

                        chunk = url_handle.read(chunk_size)

                        if not chunk:
                            break

                        f_output.write(chunk)

                    f_output.close()

                    # Rename the downloaded file
                    if not os.path.isfile(path):
                        os.rename(temp_path, path)

                        # Save downloaded file info in track file.
                        self.__addFileMetadataToDownloadTrack(df[1], key, etag, last_modified)

            except url_handle.error.URLError as e:
                #print "Timed out..."
                time.sleep(1)

            except IOError as e:

                if e.errno == errno.EPERM:
                    self.LOG.error("Permission denied to write the file to filesystem !!!")
                    time.sleep(10)

                elif e.errno == errno.ENOSPC:
                    self.LOG.error("No space left on device !!!")
                    time.sleep(10)

            except socket.timeout:

                #print "Trying connecting again to %s" % url
                time.sleep(1)

            else:
                return


    def __addFileMetadataToDownloadTrack(self, router_name, file_url, etag, file_modification_date):

        try:
            file_path = os.path.join(self.route_view_dir_path, router_name, NAME_OF_DOWNLOAD_TRACK_FILE)

            # Read the corresponding json file.
            with open(file_path) as f:
                data = json.load(f)

            data[file_url] = {"e-tag": etag, "file_modification_date": file_modification_date}

            # Write the data object to the corresponding json file.
            with open(file_path, 'w') as f:
                json.dump(data, f, sort_keys=True, indent=4)

        except:
            print (sys.exc_info()[0])

    def __checkFileMetadataInDownloadTrack(self, router_name, file_url, etag, file_modification_date):

        try:
            file_path = os.path.join(self.route_view_dir_path, router_name, NAME_OF_DOWNLOAD_TRACK_FILE)

            # Add file metadata to the corresponding file.
            with open(file_path) as f:
                data = json.load(f)

            if not data.get(file_url) is None:
                if data[file_url]['e-tag'] == etag and data[file_url]['file_modification_date'] == file_modification_date:
                    return True

                else:
                    return False

            else:
                return False

        except:
            print (sys.exc_info()[0])


class RouteDataSynchronizer(Process):

    def __init__(self, cfg, log_queue, sync_mutex, router_name):
        Process.__init__(self)
        self._stopped = False
        self.cfg = cfg
        self.LOG = None
        self.router_name = router_name
        self.sync_mutex = sync_mutex
        self._log_queue = log_queue

        self.LOG = init_mp_logger("route_views_synchronizer", self._log_queue)

        try:

            self.manager = Manager()

            self.web_address = cfg['router_data']['route_views_sync']['web_source_address']
            self.route_view_dir_path = cfg['router_data']['master_directory_path']

            self.downloader_thread = None
            self.download_queue = None

            self.__createDirIfNotExist(os.path.join(self.route_view_dir_path, router_name))
            self.__createDownloadTrackFile(router_name)

            # Create a queue for the router.
            self.download_queue = self.manager.Queue(
                self.cfg['router_data']['route_views_sync']['max_download_queue_size'])

            self.__createDownloaderThread(self.router_name)

        except IOError as e:
            print (e)

    def run(self):
        """ Override """

        try:

            while not self.stopped():

                # Lock the mutex
                self.sync_mutex.acquire()

                routers = self.getListOfRouters(self.web_address)

                router_exists = False

                router = None
                for r,k in routers:
                    if r == self.router_name:
                        router = (r,k)
                        router_exists = True

                if not router_exists:
                    self.LOG.error("'%s' is not a valid router on routeviews.org" % self.router_name)
                    sys.exit(2)

                if not self.stopped():

                    url = self.web_address + router[1] + '/'
                    router_name = router[0]

                    html = RouteDataSynchronizer.makeHTTPRequest(url)

                    if html.find('alt="[DIR]"') != -1:

                        # Directory listing html.
                        file_list_html = html.split('alt="[DIR]"></td><td><a href="')
                        del file_list_html[0]

                        for n, l in enumerate(file_list_html):
                            file_list_html[n] = l[:l.find('">')]

                        file_list_html.sort(reverse=True)

                        for link in file_list_html:

                            if link != 'SH_IP_BGP/' and not self.stopped():
                                url_link = url + link

                                result = self.__findLatestMrtFiles(url_link, router_name)

                                if result is not None:
                                    break

                self.download_queue.join()

                # Unlock the mutex
                self.sync_mutex.release()

                # Waits for 5 min
                time.sleep(10)

        except KeyboardInterrupt:
            print ("Stopped by user")
            self.stop()

        except (EOFError, IOError) as e:
            print (e)

    def __findLatestMrtFiles(self, url, router_name):

        date = None

        try:

            html = RouteDataSynchronizer.makeHTTPRequest(url)

            if html.find('alt="[DIR]"') != -1:
                # Directory listing html.
                file_list_html = html.split('alt="[DIR]"></td><td><a href="')
                del file_list_html[0]

                l = file_list_html[0]
                link = l[:l.find('">')]
                url_link = url + link

                if link == "RIBS/":

                    date = self.__findLatestRibFile(url_link, router_name)

                l = file_list_html[1]
                link = l[:l.find('">')]
                url_link = url + link

                if link == "UPDATES/" and date is not None:

                    self.__findLatestUpdateFiles(url_link, router_name, date)

        except IOError as e:
            print (e)

        except KeyboardInterrupt:
            self.stop()

        finally:
            return date

    def __findLatestRibFile(self, url, router_name):

        date = None

        try:

            html = RouteDataSynchronizer.makeHTTPRequest(url)

            if html.find('alt="[   ]"') != -1:

                # Last depth directory, it has list of mrt files.
                file_list_html = html.split('alt="[   ]"></td><td><a href="')
                del file_list_html[0]

                l = file_list_html[-1]

                link = l[:l.find('">')]

                # Check if file exists in the router's download track file.
                url_link = url + link
                url_parts = url_link.split("/")

                # Search the link in the download track file.
                # If it does not exist, then delete whole router directory and create again.

                file_path = os.path.join(self.route_view_dir_path, router_name, NAME_OF_DOWNLOAD_TRACK_FILE)

                # Add file metadata to the corresponding file.
                with open(file_path) as f:
                    data = json.load(f)

                path_parts = url_link.split("/")

                key = os.path.join(path_parts[-3], path_parts[-2], path_parts[-1])

                if data.get(key) is None:
                    self.__createDirIfNotExist(
                        os.path.join(self.route_view_dir_path, router_name, url_parts[-3], url_parts[-2]))

                    self.download_queue.put((url_link, router_name))

                # Parse date of the file.
                tokens = link.split('.')
                date = tokens[1] + tokens[2]
                date = datetime.datetime(int(date[0:4]), int(date[4:6]), int(date[6:8]), int(date[8:10]), int(date[10:]))

        except IOError as e:
            print (e)

        except KeyboardInterrupt:
            self.stop()

        finally:
            return date

    def __findLatestUpdateFiles(self, url, router_name, rib_date):

        try:

            html = RouteDataSynchronizer.makeHTTPRequest(url)

            if html.find('alt="[   ]"') != -1:
                # Last depth directory, it has list of mrt files.
                file_list_html = html.split('alt="[   ]"></td><td><a href="')
                del file_list_html[0]

                for l in file_list_html:

                    link = l[:l.find('">')]

                    # Check if file exists in the router's download track file.
                    url_link = url + link
                    url_parts = url_link.split("/")

                    # Parse date of the file.
                    tokens = link.split('.')
                    date = tokens[1] + tokens[2]
                    date = datetime.datetime(int(date[0:4]), int(date[4:6]), int(date[6:8]), int(date[8:10]),
                                             int(date[10:]))

                    if date >= rib_date:

                        self.__createDirIfNotExist(os.path.join(self.route_view_dir_path, router_name, url_parts[-3], url_parts[-2]))

                        self.download_queue.put((url_link, router_name))

        except IOError as e:
            print (e)

        except KeyboardInterrupt:
            pass

    def __createDirIfNotExist(self, path):

        while True:

            try:
                if not os.path.exists(path):
                    os.makedirs(path)

            except OSError:
                pass

            else:
                break

    def __createDownloaderThread(self, router_name):

        t = Downloader_Thread(self.download_queue, self.route_view_dir_path, router_name, self.LOG)
        t.setDaemon(True)
        t.start()

        self.downloader_thread = t

    @staticmethod
    def getListOfRouters(web_address):

        router_list = []

        html = RouteDataSynchronizer.makeHTTPRequest(web_address)

        list_start = html.find("<LI>")
        list_end = html.find("</LI>")

        # Get routers html block.
        all_routers_html = html[list_start+4:list_end-6]

        # Get list of router htmls.
        list_of_routers_html = all_routers_html.split("<br>")

        # Delete last element because last link is not a router.
        del list_of_routers_html[len(list_of_routers_html)-1]

        for r in list_of_routers_html:
            router_link = r[r.find('HREF="') + 6:r.find('">')]

            start_index_name = r.rfind('quagga bgpd') + 18

            end_index_1 = r.find(')', start_index_name)
            end_index_2 = r.find(' ', start_index_name)

            router_name = r[start_index_name:min(end_index_1,end_index_2)].strip()

            router_list.append((router_name, router_link))

        return router_list

    def __createDownloadTrackFile(self, router_name):

        while True:

            try:
                if not os.path.isdir(os.path.join(self.route_view_dir_path, router_name)):
                    os.makedirs(os.path.join(self.route_view_dir_path, router_name))

                if not os.path.isfile(os.path.join(self.route_view_dir_path, router_name, NAME_OF_DOWNLOAD_TRACK_FILE)):
                    empty_dict = dict()

                    with open(os.path.join(self.route_view_dir_path, router_name, NAME_OF_DOWNLOAD_TRACK_FILE), 'w') as outfile:
                        json.dump(empty_dict, outfile, sort_keys=True, indent=4)

            except OSError:
                pass

            else:
                break

    @staticmethod
    def makeHTTPRequest(web_address):

        while True:

            try:
                response = urlopen(web_address, None, 5.0)
                data = response.read()

            except (response.error.URLError, IOError):

                #print "Trying connecting again to %s" % web_address
                time.sleep(2)

            else:
                return data

    def stop(self):
        self._stopped = True

    def stopped(self):
        return self._stopped