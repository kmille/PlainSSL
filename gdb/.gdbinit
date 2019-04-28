python
def read_as_hex(name, size):
    addr = gdb.parse_and_eval(name).address
    data = gdb.selected_inferior().read_memory(addr, size)
    return ''.join('%02X' % ord(x) for x in data)

def pm(ssl='s'):
    mk = read_as_hex('%s->session->master_key' % ssl, 48)
    cr = read_as_hex('%s->s3->client_random' % ssl, 32)
    print('CLIENT_RANDOM %s %s' % (cr, mk))
end

#set sysroot /no/such/file
#set solib-search-path /home/kmille/projects/hooking-ssl/gdb/openssl:/usr/lib:/lib
#directory  /home/kmille/projects/hooking-ssl/gdb/openssl/
set breakpoint pending on
break SSL_connect
run
