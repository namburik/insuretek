ps -eaf|grep src|grep python|awk '{print $2}'|xargs kill -9

for _port in 6001 8091 8092 8093; do
  lsof -i:$_port|awk '{print $2}'|tail -1|xargs kill -9 2>/dev/null
done

if [ "$LOGNAME" = "ec2-user" ]; then
  cd /home/ec2-user/app/insuretek
  git status
  git pull origin main
  export PYTHONPATH=/home/ec2-user/app/insuretek/src
  export SERVER_HOST=0.0.0.0
  alias python=python3.11
  cp src/shared/config/.env_prod src/shared/config/.env
  LOG_DIR=/home/ec2-user/logs
  LOG_DATE=$(date +%Y%m%d_%H%M%S)

  echo "Building React UI..."
  cd src/web_gateway/react && npm ci --silent && npm run build && cd ../../..
  echo "React build done."

  python3.11 src/mcp_server/insuretek_core_data.py     >> ${LOG_DIR}/insuretek_core_data_${LOG_DATE}.log     2>&1 &
  python3.11 src/mcp_server/insuretek_agent_workflow.py >> ${LOG_DIR}/insuretek_agent_workflow_${LOG_DATE}.log 2>&1 &
  python3.11 src/mcp_server/insuretek_semantic_context.py >> ${LOG_DIR}/insuretek_semantic_context_${LOG_DATE}.log 2>&1 &
  python3.11 src/web_gateway/app.py >> ${LOG_DIR}/uvicorn_react_${LOG_DATE}.log 2>&1 &

  echo "Started. Logs in ${LOG_DIR}/ (suffix: ${LOG_DATE})"
  find ${LOG_DIR}/ -name "*.log" -mmin +60 -delete 2>/dev/null
else
  # ── Local dev ──────────────────────────────────────────────────────
  # Web + APIs on :6001  →  open http://localhost:6001
  cd /Users/namburi/work_poc/4-insuretek
  export PYTHONPATH=/Users/namburi/work_poc/4-insuretek/src

  echo "Building React UI..."
  cd src/web_gateway/react && npm ci --silent && npm run build && cd ../../..
  echo "React build done."

  python src/mcp_server/insuretek_core_data.py   2>&1 &
  python src/mcp_server/insuretek_agent_workflow.py 2>&1 &
  python src/mcp_server/insuretek_semantic_context.py 2>&1 &
  python src/web_gateway/app.py 2>&1 &

fi
