
import ipaddr


class IPNetwork:
    def __init__(self, net):
        self._net = net

    def __getattr__(self, name):
        if name == "with_prefixlen":
            return self._net.prefixlen

        return getattr(self._net, name)


def ip_address(ip):
    return ipaddr.IPAddress(ip)


def ip_network(net):
    return IPNetwork(ipaddr.IPNetwork(net))


AddressValueError = ipaddr.AddressValueError
IPv4Address = ipaddr.IPv4Address
IPv6Address = ipaddr.IPv6Address
