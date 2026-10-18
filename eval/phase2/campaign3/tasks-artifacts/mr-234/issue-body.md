# Issue body (mysql-router-operators#234), as filed by H-M-Quang-Ngo on 2026-09-30.

## Title: mysql-router-k8s - per-minute log rotation makes promtail CPU grow with pod age

## Body

<!-- Thank you for submitting a bug report! All fields are required unless marked optional. -->

## Steps to reproduce
- Deploy mysql-router-k8s (8.0/stable rev 916) with a logging relation to Loki. 
- Inside the `mysql-router` container, watch `/var/log/mysqlrouter/archive_mysqlrouter` grow by 1 per minute, most are empty files.
- Watch CPU consumption of promtail process and CPU load increasing

## Expected behavior
Promtail CPU should stay roughly constant for a router with little log output, independent of pod age. Otherwise for testing environment with little CPU, or for Sunbeam control plane where there are lots of mysql-router units, nodes get CPU overloaded very quickly.

## Actual behavior
- Every mysql-router-k8s pod runs a promtail whose CPU usage grows steadily overtime
- On my 8-vCPU nodes, ~13 router pods aged ~2.5 days used ~3.7 cores for promtail alone (node load average ~50)
- Log rotation runs every minute, and keeps a new file each time even if the log is empty 
- promtail's scrape path `/var/log/mysqlrouter/**/*log*`  (from `/etc/promtail/promtail_config.yaml`) includes the archive folder, so it watches every rotated file.

## Versions
<!-- Model version from `juju status` -->
Juju agent: 3.6.28

<!-- "Kubernetes" or "machines" -->
Cloud type: Kubernetes
<!--
For microk8s, run `microk8s version`
For LXD, run `lxd version`
For other clouds, please provide any version information
-->
Cloud: K8s control plane for Sunbeam 2024.01/stable.

<!-- App revision from `juju status` or (advanced) commit hash -->
mysql/mysql-k8s charm revision: `mysql-k8s rev 423`
mysql-router/mysql-router-k8s charm revision: `mysql-router-k8s rev 916`

## Log output
<!-- Please enable debug logging by running `juju model-config logging-config="<root>=INFO;unit=DEBUG"` (if possible) -->
<!-- Then, run `juju debug-log --replay > log.txt` and upload "log.txt" file here -->
Juju debug log: N/A

<!-- (Optional) Copy the logs that are relevant to the bug & paste inside triple backticks below -->


## Additional context
<!-- (Optional) Add any additional information here -->
