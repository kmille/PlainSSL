# look into ssl traffic

credits: https://security.stackexchange.com/questions/80158/extract-pre-master-keys-from-an-openssl-application

## LD_PRELOAD
### How to 
1. cc sslkeylog.c -shared -o libsslkeylog.so -fPIC -ldl 
2. sudo tcpdump -i any port 443 -w out
3. SSLKEYLOGFILE=premaster.txt LD_PRELOAD=./libsslkeylog.so curl https://heise.de
4. wireshark -o ssl.keylog_file:premaster.txt out

