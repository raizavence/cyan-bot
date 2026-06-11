#!/bin/bash
# Mata qualquer instância rodando e sobe uma nova
PIDFILE="/root/cyan-bot/cyan.pid"

if [ -f "$PIDFILE" ]; then
  OLD_PID=$(cat "$PIDFILE")
  kill "$OLD_PID" 2>/dev/null && echo "Instância anterior (PID $OLD_PID) encerrada."
  sleep 1
fi

# Mata TODOS os processos com main.py no caminho, independente de como foram iniciados
kill $(ps aux | grep "main.py" | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 1

nohup /root/cyan-bot/venv/bin/python3 /root/cyan-bot/main.py >> /root/cyan-bot/cyan.log 2>&1 &
echo $! > "$PIDFILE"
echo "Cyan iniciado — PID $(cat $PIDFILE)"
