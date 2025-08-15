# Run solara on the remote machine on  0.0.0.0 & port 80

From solara folder run at cl:

SOLARA_APP=traceroute_map.py uvicorn --workers 4 --host 0.0.0.0 --port 80 solara.server.starlette:app