#!/bin/bash
systemctl restart cyan && journalctl -u cyan -n 8 --no-pager
