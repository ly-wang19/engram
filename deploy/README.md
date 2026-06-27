# Engram Production Service

Production runs from `/home/ubuntu/engram-memory` on port `8456`.

Install or refresh the unit:

```bash
sudo cp deploy/engram.service /etc/systemd/system/engram.service
sudo systemctl daemon-reload
sudo systemctl enable --now engram.service
sudo systemctl restart engram.service
```

Runtime configuration is read from `/home/ubuntu/engram-memory/.env`. Logs are appended to
`/home/ubuntu/engram-memory/engram.log` and are also visible with:

```bash
sudo journalctl -u engram.service -n 100 --no-pager
```
