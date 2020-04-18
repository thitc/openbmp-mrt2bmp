
import gzip
import bz2
import struct
from struct import unpack
import mrt2bmp.HelperClasses
import binascii
import socket

GZIP_HEADER = b'\x1f\x8b'
BZ2_HEADER = b'\x42\x5a\x68'

MRT_TYPES = {
    11:'OSPFv2',
    12:'TABLE_DUMP',
    13:'TABLE_DUMP_V2',
    16:'BGP4MP',
    17:'BGP4MP_ET',
    32:'ISIS',
    33:'ISIS_ET',
    48:'OSPFv3',
    49:'OSPFv3_ET'
}

TABLE_DUMP_V2_SUBTYPES = {
    1:'PEER_INDEX_TABLE',
    2:'RIB_IPV4_UNICAST',
    3:'RIB_IPV4_MULTICAST',
    4:'RIB_IPV6_UNICAST',
    5:'RIB_IPV6_MULTICAST',
    6:'RIB_GENERIC',
}

BGP4MP_SUBTYPES = {
    0:'BGP4MP_STATE_CHANGE',
    1:'BGP4MP_MESSAGE',
    2:'BGP4MP_ENTRY',             # Deprecated in RFC6396
    3:'BGP4MP_SNAPSHOT',          # Deprecated in RFC6396
    4:'BGP4MP_MESSAGE_AS4',
    5:'BGP4MP_STATE_CHANGE_AS4',
    6:'BGP4MP_MESSAGE_LOCAL',
    7:'BGP4MP_MESSAGE_AS4_LOCAL',
}

ADDRESS_FAMILY = {
    1:'IPv4',
    2:'IPv6'
}

SAFI_TYPES = {
    1:'UNICAST',
    2:'MULTICAST'
}

BGP_ATTRIBUTES = {
    1:'ORIGIN',
    2:'AS_PATH',
    3:'NEXT_HOP',
    4:'MULTI_EXIT_DISC',
    5:'LOCAL_PREF',
    6:'ATOMIC_AGGREGATE',
    7:'AGGREGATOR',
    8:'COMMUNITY',             # Defined in RFC1997
    9:'ORIGINATOR_ID',         # Defined in RFC4456
    10:'CLUSTER_LIST',         # Defined in RFC4456
    11:'DPA',                  # Deprecated in RFC6938
    12:'ADVERTISER',           # Deprecated in RFC6938
    13:'RCID_PATH/CLUSTER_ID', # Deprecated in RFC6938
    14:'MP_REACH_NLRI',        # Defined in RFC4760
    15:'MP_UNREACH_NLRI',      # Defined in RFC4760
    16:'EXTENDED_COMMUNITIES', # Defined in RFC4360
    17:'AS4_PATH',             # Defined in RFC6793
    18:'AS4_AGGREGATOR',       # Defined in RFC6793
    26:'AIGP',                 # Defined in RFC7311
    128:'ATTR_SET',            # Defined in RFC6368
}

class MrtFileException(Exception):

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)

class MrtParser():

    def __init__(self, file_path):

        f = open(file_path, 'rb')

        file_header = f.read(max(len(GZIP_HEADER), len(BZ2_HEADER)))

        if file_header.startswith(BZ2_HEADER):
            self.f = bz2.BZ2File(file_path, 'rb')

        elif file_header.startswith(GZIP_HEADER):
            self.f = gzip.GzipFile(file_path, 'rb')

        else:
            self.f = open(file_path, 'rb')

    def __iter__(self):
        return self

    def __next__(self):

        entry = dict()

        # Parse mrt header.
        entry['mrt_header'] = {}
        self.parseMrtHeader(entry['mrt_header'])

        # Parse mrt entry.
        entry['mrt_entry'] = {}
        msg_len = entry['mrt_header']['length']
        msg_type = entry['mrt_header']['type']
        msg_subtype = entry['mrt_header']['subtype']

        self.parseMrtEntry( entry['mrt_entry'], msg_len, msg_type, msg_subtype)

        return entry

    def parseMrtHeader(self, mrt_header):

        try:
            buf = self.f.read(12)

            if len(buf) == 0:
                self.close()

            elif len(buf) < 12:
                raise MrtFileException("Mrt Header length is %i < 12" % len(buf))

            """
            MRT HEADER:

            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                           Timestamp                           |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |             Type              |            Subtype            |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                             Length                            |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                      Message... (variable)
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

            """

            mrt_header['timestamp'], mrt_header['type'], mrt_header['subtype'], mrt_header['length'] = unpack('!I H H I', buf)

        except MrtFileException as e:
            print ('Mrt File exception occurred: ', e.value)
            self.close()

    def parseMrtEntry(self, mrt_message, msg_len, msg_type, msg_subtype):

        try:
            buf = self.f.read(msg_len)

            if len(buf) < msg_len:
                raise MrtFileException(("Mrt message (data) length is %d < %d (message length)", len(buf), msg_len))

            if MRT_TYPES[msg_type] == 'TABLE_DUMP_V2':
                self.parseTableDumpV2(buf, mrt_message, msg_len, msg_type, msg_subtype)

            elif MRT_TYPES[msg_type] == 'BGP4MP':
                self.parseBGP4MP(buf, mrt_message, msg_len, msg_type, msg_subtype)

        except MrtFileException as e:
            print ('Mrt File exception occurred: ', e.value)
            self.close()

    def parseTableDumpV2(self, buf, mrt_message, msg_len, msg_type, msg_subtype):

        # AFI/SAFI-Specific RIB Subtypes
        if TABLE_DUMP_V2_SUBTYPES[msg_subtype] == "RIB_IPV4_UNICAST" or \
                        TABLE_DUMP_V2_SUBTYPES[msg_subtype] == "RIB_IPV6_UNICAST":

            p = 0

            address_family = None
            safi = 1
            raw_nlri = b""
            m = None

            if TABLE_DUMP_V2_SUBTYPES[msg_subtype] == "RIB_IPV4_UNICAST":
                # Address family is IPv4.
                address_family = 1
                m = 4

            elif TABLE_DUMP_V2_SUBTYPES[msg_subtype] == "RIB_IPV6_UNICAST":
                # Address family is IPv6.
                address_family = 2
                m = 16

            """
            RIB Entry Header:

            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                         Sequence Number                       |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           | Prefix Length |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                        Prefix (variable)                      |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |         Entry Count           |  RIB Entries (variable)
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            """

            raw_nlri += buf[p+4:p+5]

            mrt_message['seq_number'], mrt_message['prefix_len'] = \
                unpack('!I B', buf[p:p + 5])
            p += 5

            # Parse prefix.
            n = mrt_message['prefix_len']
            prefix_length_in_bytes = int(mrt_message['prefix_len'] / 8) + (mrt_message['prefix_len'] % 8 > 0)
            raw_nlri += buf[p:p + prefix_length_in_bytes]

            n = m if n < 0 else (n + 7) // 8

            if ADDRESS_FAMILY[address_family] == "IPv4":
                # Address family is IPv4.
                mrt_message['prefix'] = socket.inet_ntop(socket.AF_INET, buf[p:p+n] + b'\x00' * (m - n))

            elif ADDRESS_FAMILY[address_family] == "IPv6":
                # Address family is IPv6.
                mrt_message['prefix'] = socket.inet_ntop(socket.AF_INET6, buf[p:p+n] + b'\x00' * (m - n))

            p += prefix_length_in_bytes

            mrt_message['entry_count'] = unpack('!H', buf[p:p + 2])[0]
            p += 2

            mrt_message['raw_prefix_nlri'] = raw_nlri

            mrt_message['rib_entries'] = []

            if mrt_message['entry_count'] > 0:
                self.parseRibEntries(buf[p:], mrt_message['rib_entries'], address_family, safi, raw_nlri)

        elif TABLE_DUMP_V2_SUBTYPES[msg_subtype] == "PEER_INDEX_TABLE":
            p = 0

            """
            PEER_INDEX_TABLE Subtype:

            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                      Collector BGP ID                         |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |       View Name Length        |     View Name (variable)      |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |          Peer Count           |    Peer Entries (variable)
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

            """

            mrt_message['collector_id'] = socket.inet_ntop(socket.AF_INET, buf[p:p+4])
            p += 4

            mrt_message['view_length'] = struct.unpack("!H", buf[p:p+2])[0]
            p += 2

            mrt_message['view_name'] = buf[p:p+mrt_message['view_length']]
            p += mrt_message['view_length']

            mrt_message['peer_count'] = struct.unpack("!H", buf[p:p+2])[0]
            p += 2

            mrt_message['peer_list'] = []

            self.parsePeerEntries(buf[p:], mrt_message['peer_list'], mrt_message['peer_count'])

    def parsePeerEntries(self, buf, peer_list, peer_count):

        p = 0

        """
        Peer Entry:

        0                   1                   2                   3
        0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |   Peer Type   |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                         Peer BGP ID                           |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                   Peer IP Address (variable)                  |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                        Peer AS (variable)                     |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        """

        while p < len(buf):

            peer_entry = dict()

            peer_entry['type'] = struct.unpack("!B", buf[p:p+1])[0]
            p += 1

            peer_entry['bgp_id'] = socket.inet_ntop(socket.AF_INET, buf[p:p+4])
            p += 4

            afi = None
            ip_length = 0
            asn = None

            if peer_entry['type'] == 0:
                peer_entry['as_number_size'] = 2
                peer_entry['ip_address_family'] = 'IPv4'

                afi = socket.AF_INET
                ip_length = 4
                asn = "!H"

            elif peer_entry['type'] == 1:
                peer_entry['as_number_size'] = 2
                peer_entry['ip_address_family'] = 'IPv6'

                afi = socket.AF_INET6
                ip_length = 16
                asn = "!H"

            elif peer_entry['type'] == 2:
                peer_entry['as_number_size'] = 4
                peer_entry['ip_address_family'] = 'IPv4'

                afi = socket.AF_INET
                ip_length = 4
                asn = "!I"

            elif peer_entry['type'] == 3:
                peer_entry['as_number_size'] = 4
                peer_entry['ip_address_family'] = 'IPv6'
                afi = socket.AF_INET6
                ip_length = 16
                asn = "!I"

            peer_entry['ip_address'] = socket.inet_ntop(afi, buf[p:p+ip_length])
            p += ip_length

            peer_entry['asn'] = struct.unpack(asn, buf[p:p+peer_entry['as_number_size']])[0]
            p += peer_entry['as_number_size']

            peer_list.append(peer_entry)

    def parseRibEntries(self, buf, rib_entries, address_family, safi, raw_nlri):

        p = 0

        """
        RIB Entry:

        0                   1                   2                   3
        0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |         Peer Index            |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                         Originated Time                       |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |      Attribute Length         |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                    BGP Attributes... (variable)
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        """

        while p < len(buf):

            rib_entry = dict()

            rib_entry['peer_index'], rib_entry['originated_time'], rib_entry['attribute_length'] = \
                unpack('!H I H', buf[p:p+8])
            p += 8

            # Parse BGP attributes.
            rib_entry['bgp_attribute_list'] = []
            rib_entry['raw_bgp_attributes'] = []
            rib_entry['raw_mp_reach_nlri'] = dict()

            self.parseBgpAttributes(buf[p:p+rib_entry['attribute_length']], rib_entry['bgp_attribute_list'],
                                   rib_entry['raw_bgp_attributes'], rib_entry['attribute_length'], address_family, safi, raw_nlri, rib_entry['raw_mp_reach_nlri'])

            p += rib_entry['attribute_length']

            # Set NEW bgp path attributes length.
            rib_entry['raw_attribute_length'] = len(rib_entry['raw_bgp_attributes'])

            rib_entries.append(rib_entry)

    def parseBgpAttributes(self, buf, bgp_attribute_list, raw_bgp_attributes, attr_length, address_family, safi, raw_nlri, raw_mp_reach_nlri):

        p = 0

        while p < attr_length:

            start = p

            bgp_path_attribute = dict()

            bgp_path_attribute['flag'], bgp_path_attribute['type'] = unpack('!B B', buf[p:p+2])
            p += 2

            if bgp_path_attribute['flag'] & 0x01 << 4:

                bgp_path_attribute['len'] = unpack('!H', buf[p:p+2])[0]
                p += 2

            else:
                bgp_path_attribute['len'] = unpack('!B', buf[p:p+1])[0]
                p += 1

            end = p + bgp_path_attribute['len']

            """
                Only NEXT_HOP or MP_REACH_NLRI exists, both of them cannot co-exist in path attributes.
            """
            # Parse out NEXT_HOP attribute
            if BGP_ATTRIBUTES[bgp_path_attribute['type']] == 'NEXT_HOP':

                if ADDRESS_FAMILY[address_family] == "IPv4":
                    # Address family is IPv4.
                    raw_next_hop = buf[p:p + 4]
                    raw_next_hop_length = struct.pack("!B", 4)
                    p += 4

                elif ADDRESS_FAMILY[address_family] == "IPv6":
                    # Address family is IPv6.
                    raw_next_hop = buf[p:p + 16]
                    raw_next_hop_length = struct.pack("!B", 16)
                    p += 16

                # Add MP_REACH_NLRI attribute to raw attribute value.
                raw_mp_reach_nlri['value'] = self.crateMpReachNlriAttributeRaw(address_family, safi, (raw_next_hop_length + raw_next_hop), b"")

            # Parse out MP_REACH attribute
            elif BGP_ATTRIBUTES[bgp_path_attribute['type']] == 'MP_REACH_NLRI':

                """
                There is one exception to the encoding of BGP attributes for the BGP
                MP_REACH_NLRI attribute (BGP Type Code 14) [RFC4760].  Since the AFI,
                SAFI, and NLRI information is already encoded in the RIB Entry Header
                or RIB_GENERIC Entry Header, only the Next Hop Address Length and
                Next Hop Address fields are included.
                """

                raw_next_hop_attr = buf[p:p+bgp_path_attribute['len']]

                p += bgp_path_attribute['len']
                raw_mp_reach_nlri['value'] = self.crateMpReachNlriAttributeRaw(address_family, safi, raw_next_hop_attr, b"")

            # No parsing for other attributes.
            else:
                p += bgp_path_attribute['len']
                raw_bgp_attributes.append(buf[start:end])

            bgp_attribute_list.append(bgp_path_attribute)


    def getMpReachNlriAttributeHeader(self, attr_length):

        # Crate attribute flag.
        # b'10010000' as big endian = 144
        attr_flag = 144

        # Create attribute type code.
        attr_type_code = 14

        return struct.pack("!B B H", attr_flag, attr_type_code, attr_length)

    def crateMpReachNlriAttributeRaw(self, afi, safi, raw_next_hop, raw_nlri):

        """
        Multiprotocol Reachable NLRI - MP_REACH_NLRI (Type Code 14):

        +---------------------------------------------------------+
        | Address Family Identifier (2 octets)                    |
        +---------------------------------------------------------+
        | Subsequent Address Family Identifier (1 octet)          |
        +---------------------------------------------------------+
        | Length of Next Hop Network Address (1 octet)            |
        +---------------------------------------------------------+
        | Network Address of Next Hop (variable)                  |
        +---------------------------------------------------------+
        | Reserved (1 octet)                                      |
        +---------------------------------------------------------+
        | Network Layer Reachability Information (variable)       |
        +---------------------------------------------------------+

        """

        reserved_raw = struct.pack("!B", 0)

        attr_body = struct.pack("!H B", afi, safi) + raw_next_hop + reserved_raw + raw_nlri

        return attr_body

    def parseBGP4MP(self, buf, mrt_message, msg_len, msg_type, msg_subtype):

        if (BGP4MP_SUBTYPES[msg_subtype] == "BGP4MP_MESSAGE_AS4"
            or BGP4MP_SUBTYPES[msg_subtype] == "BGP4MP_MESSAGE"):

            p = 0

            """
            BGP4MP_MESSAGE:
            
            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |         Peer AS Number        |        Local AS Number        |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |        Interface Index        |        Address Family         |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                      Peer IP Address (variable)               |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                      Local IP Address (variable)              |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                    BGP Message... (variable)
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            
            BGP4MP_MESSAGE_AS4:

            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                         Peer AS Number                        |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                         Local AS Number                       |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |        Interface Index        |        Address Family         |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                      Peer IP Address (variable)               |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                      Local IP Address (variable)              |
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
           |                    BGP Message... (variable)
           +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

            """
            offset = 8

            if BGP4MP_SUBTYPES[msg_subtype] == "BGP4MP_MESSAGE_AS4":
                offset = 8
                mrt_message['peer_as'], mrt_message['local_as'] = unpack("!II", buf[p: p + offset])

            else:
                offset = 4
                mrt_message['peer_as'], mrt_message['local_as'] = unpack("!HH", buf[p: p + offset])

            p += offset

            mrt_message['interface_index'], mrt_message['address_family'] = unpack('!HH', buf[p:p+4])
            p += 4

            # IPv4
            if ADDRESS_FAMILY[mrt_message['address_family']] == "IPv4":
                mrt_message['peer_ip'] = socket.inet_ntop(socket.AF_INET, buf[p:p+4])
                p += 4

                mrt_message['local_ip'] = socket.inet_ntop(socket.AF_INET, buf[p:p+4])
                p += 4


            # IPv6
            elif ADDRESS_FAMILY[mrt_message['address_family']] == "IPv6":
                mrt_message['peer_ip'] = socket.inet_ntop(socket.AF_INET6, buf[p:p+16])
                p += 16

                mrt_message['local_ip'] = socket.inet_ntop(socket.AF_INET6, buf[p:p+16])
                p += 16

            mrt_message['raw_bgp_message'] = buf[p:]

    def close(self):
        self.f.close()
        raise StopIteration
