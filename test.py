#!/usr/bin/env python3
import requests

print(requests.get("https://heise.de", allow_redirects=False).text)
