# Migrated

The AI Trading System now lives in a separate repository and Docker Compose project:

- GitHub: https://github.com/gallif1/ai-trading-system
- Server dir: `~/ai-trading-system`
- Compose: `docker compose -p ai-trading-system …`
- URL: `http://<EC2_PUBLIC_IP>:8100` (HTTP Basic Auth)

This `trading/` directory is retained temporarily for historical reference and
data recovery. It is **no longer mounted** by Resume Agent.

Do not delete host backups under `~/trading-data-backup-*` or the Docker volume
`ai-trading-system-data` without an explicit restore plan.
