import shutil
import os
import socket
import struct


class MessageBucket():

    def __init__(self, peer, raw_path_attribute, raw_prefix_nlri, mp_reach_attribute, forward_queue):
        self.peer = peer
        self.raw_path_attribute = raw_path_attribute
        self.raw_prefixes = raw_prefix_nlri
        self.mp_reach_attribute = mp_reach_attribute

        self._forward_queue = forward_queue

    def addPrefix(self, raw_prefix):

        if len(self.raw_prefixes) >= 4000:
            self.__sendMessage()

        self.raw_prefixes += raw_prefix


    def getMpReachNlriAttributeHeader(self, attr_length):
        # Crate attribute flag.
        # b'10010000' as big endian = 144
        attr_flag = 144

        # Create attribute type code.
        attr_type_code = 14

        return struct.pack("!B B H", attr_flag, attr_type_code, attr_length)

    def finalizeBucket(self):

        if self.raw_prefixes != b"":
            self.__sendMessage()

    def __sendMessage(self):

        # 1-) Create BGP Update message
        mp_reach_length = len(self.mp_reach_attribute + self.raw_prefixes)
        bgp_update_message = BGP_Helper.createBgpUpdateMessage(b"", self.raw_path_attribute + self.getMpReachNlriAttributeHeader(mp_reach_length) + self.mp_reach_attribute + self.raw_prefixes, b"")

        # 2-) Create BGP Common header
        bgp_common_header = BGP_Helper.createBgpHeader(len(bgp_update_message), 2)

        # 3-) Create BMP Per Peer header
        bmp_per_peer_header = BMP_Helper.createBmpPerPeerHeader(0, 0, self.peer, 0, 0)

        # 4-) Create BMP Common header
        bmp_common_header = BMP_Helper.createBmpCommonHeader(3, len(bgp_update_message) +
                                                       len(bgp_common_header) + len(bmp_per_peer_header) + 6, 0)

        # Put the message in the queue.
        qm = bmp_common_header + bmp_per_peer_header + bgp_common_header + bgp_update_message

        self._forward_queue.put(qm)

        # Clear the prefixes.
        self.raw_prefixes = b""

class BGP_Helper:

    @staticmethod
    def createBgpHeader(data_length, message_type):

        """
        BGP Message Header Format:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |                                                               |
          +                                                               +
          |                                                               |
          +                                                               +
          |                           Marker                              |
          +                                                               +
          |                                                               |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |          Length               |      Type     |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

          1 - OPEN
          2 - UPDATE
          3 - NOTIFICATION
          4 - KEEPALIVE

        """

        # Marker
        marker = '\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF'

        # Length
        length = struct.pack("!H", data_length + 19)

        # Type
        type = struct.pack("!B", message_type)

        return marker + length + type

    @staticmethod
    def createBgpOpenMessage(as_number, hold_time, bgp_iden, remote_asn):

        """
        Open Message Format:

           0                   1                   2                   3
           0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
           +-+-+-+-+-+-+-+-+
           |    Version    |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |     My Autonomous System      |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |           Hold Time           |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                         BGP Identifier                        |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           | Opt Parm Len  |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                                                               |
           |             Optional Parameters (variable)                    |
           |                                                               |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        The current BGP version number is 4.

        """

        # Version
        version = struct.pack("!B", 4)

        # My Autonomous System
        as_number = 23456

        my_autonomous_system = struct.pack("!H", as_number)

        # Hold Time
        hold_time = struct.pack("!H", hold_time)

        # BGP Identifier
        if bgp_iden:
            bgp_identifier = socket.inet_pton(socket.AF_INET, bgp_iden)
        else:
            bgp_identifier = socket.inet_pton(socket.AF_INET, "0.0.0.0")

        # Optional Parameters (Add BGP Capabilities)
        octet_4_as_cap = struct.pack("!B B", 2, 6) + struct.pack("!B B I", 65, 4, remote_asn)

        # Add AFI, SAFI values to caps.
        mp_ipv4_unicast_cap = struct.pack("!B B", 2, 5) + struct.pack("!B B H B", 1, 4, 1, 1)
        mp_ipv6_unicast_cap = struct.pack("!B B", 2, 5) + struct.pack("!B B H B", 1, 4, 2, 1)

        # Optional_parameters = octet_4_as_cap + mp_ipv4_unicast_cap + mp_ipv6_unicast_cap
        optional_parameters = octet_4_as_cap + mp_ipv4_unicast_cap + mp_ipv6_unicast_cap

        # Optional Parameters Length
        optional_parameters_length = struct.pack("!B", len(optional_parameters))

        # Open Message
        open_message = version + my_autonomous_system + hold_time + bgp_identifier + optional_parameters_length + optional_parameters

        # Bgp Header
        bgp_header = BGP_Helper.createBgpHeader(len(open_message), 1)

        return bgp_header + open_message

    @staticmethod
    def createBgpUpdateMessage(withdrawn_routes, path_attributes, nlri):

        """
        Bgp Update Message:

        +-----------------------------------------------------+
      |   Withdrawn Routes Length (2 octets)                |
      +-----------------------------------------------------+
      |   Withdrawn Routes (variable)                       |
      +-----------------------------------------------------+
      |   Total Path Attribute Length (2 octets)            |
      +-----------------------------------------------------+
      |   Path Attributes (variable)                        |
      +-----------------------------------------------------+
      |   Network Layer Reachability Information (variable) |
      +-----------------------------------------------------+

        """

        bgp_update_message = struct.pack("!H", len(withdrawn_routes)) + withdrawn_routes + \
                              struct.pack("!H", len(path_attributes)) + path_attributes + nlri

        return bgp_update_message


class BMP_Helper:

    @staticmethod
    def createBmpCommonHeader(version, data_length, msg_type):

        """
        BMP Common Header:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
         +-+-+-+-+-+-+-+-+
         |    Version    |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                        Message Length                         |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |   Msg. Type   |
         +---------------+

        Message Types:
          *  Type = 0: Route Monitoring
          *  Type = 1: Statistics Report
          *  Type = 2: Peer Down Notification
          *  Type = 3: Peer Up Notification
          *  Type = 4: Initiation Message
          *  Type = 5: Termination Message
          *  Type = 6: Route Mirroring Message

        """

        return struct.pack("!B I B", version, data_length, msg_type)

    @staticmethod
    def createBmpPerPeerHeader(p_type, p_dist, peer, ts_s, ts_ms):

        """
        BMP Peer Header:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |   Peer Type   |  Peer Flags   |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |         Peer Distinguisher (present based on peer type)       |
         |                                                               |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                 Peer Address (16 bytes)                       |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                           Peer AS                             |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                         Peer BGP ID                           |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                    Timestamp (seconds)                        |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                  Timestamp (microseconds)                     |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

          *  Peer Type = 0: Global Instance Peer
          *  Peer Type = 1: RD Instance Peer
          *  Peer Type = 2: Local Instance Peer

          Peer Flags:

         0 1 2 3 4 5 6 7
         +-+-+-+-+-+-+-+-+
         |V|L|A| Reserved|
         +-+-+-+-+-+-+-+-+
        """

        V_FLAG = 0x80
        L_FLAG = 0x40
        A_FLAG = 0x20

        peer_flags = 0x00

        # V bit
        if peer['ip_address_family'] == "IPv4":
            # Zero added to peer_flags
            peer_address = struct.pack('!12x') + socket.inet_pton(socket.AF_INET, peer['ip_address'])

        elif peer['ip_address_family'] == "IPv6":
            peer_flags |= V_FLAG
            peer_address = socket.inet_pton(socket.AF_INET6, peer['ip_address'])

        # L bit
            peer_flags |= L_FLAG

        if peer['as_number_size'] == 2:
            peer_flags |= A_FLAG

        bgp_id = socket.inet_pton(socket.AF_INET, peer['bgp_id'])

        return struct.pack("!B B Q", p_type, peer_flags, p_dist) + peer_address + struct.pack("!I", int(peer['asn'])) \
            + bgp_id + struct.pack("!I I", ts_s, ts_ms)

    @staticmethod
    def createPeerUpMessage(peer, collector_id):

        # peer up message = Common header + per-peer header + peer up notification
        peer_up_notification = BMP_Helper.createPeerUpNotification(peer, collector_id)

        per_peer_header = BMP_Helper.createBmpPerPeerHeader(0, 0, peer, 0, 0)

        common_header = BMP_Helper.createBmpCommonHeader(3, len(per_peer_header) + len(peer_up_notification) + 6, 3)

        return common_header + per_peer_header + peer_up_notification

    @staticmethod
    def createPeerDownMessage(peer):

        # peer up message = Common header + per-peer header + peer up notification
        peer_down_notification = BMP_Helper.createPeerDownNotification()

        per_peer_header = BMP_Helper.createBmpPerPeerHeader(0, 0, peer, 0, 0)

        common_header = BMP_Helper.createBmpCommonHeader(3, len(per_peer_header) + len(peer_down_notification) + 6, 2)

        return common_header + per_peer_header + peer_down_notification

    @staticmethod
    def createPeerUpNotification(peer, collector_id):

        """
          Peer Up Notification:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                 Local Address (16 bytes)                      |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |         Local Port            |        Remote Port            |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                    Sent OPEN Message                          |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                  Received OPEN Message                        |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |                 Information (variable)                        |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        """

        # Local Address
        local_address = None

        if peer['ip_address_family'] == 'IPv4':
            local_address = struct.pack('!12x') + socket.inet_pton(socket.AF_INET, "0.0.0.0")

        elif peer['ip_address_family'] == 'IPv6':
            local_address = socket.inet_pton(socket.AF_INET6, "0:0:0:0:0:0:0:0")

        # Local Port
        local_port = struct.pack('!H', 0)

        # Remote Port
        remote_port = struct.pack('!H', 0)

        # Sent OPEN Message
        sent_open_message = BGP_Helper.createBgpOpenMessage(0, 0, collector_id, peer['asn'])

        # Received OPEN Message
        received_open_message = BGP_Helper.createBgpOpenMessage(peer['asn'], 0, peer['bgp_id'], 0)

        return local_address + local_port + remote_port + sent_open_message + received_open_message

    @staticmethod
    def createPeerDownNotification():

        """
        Peer Down Notification:

          0                   1                   2                   3
          0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
         +-+-+-+-+-+-+-+-+
         |    Reason     |
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
         |            Data (present if Reason = 1, 2 or 3)               |
         ~                                                               ~
         +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        """

        """
          Reason 4: The remote system closed the session without a
          notification message.  This includes any unexpected termination of
          the transport session, so in some cases both the local and remote
          systems might consider this to apply.
        """
        REASON = 4

        return struct.pack("!B", REASON)


def moveFileToTempDirectory(src_file_path, dst_dir_path):

    try:

        # Checks if dst directory exists. If not, then creates the directory structure.
        #if not os.path.isdir(dst_dir_path):
        #   os.makedirs(dst_dir_path)
        #shutil.move(src_file_path, dst_dir_path)
        os.remove(src_file_path)
        #pass


    except shutil.Error as e:
        print(e)
        pass
