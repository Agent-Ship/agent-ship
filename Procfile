# Heroku/Procfile-style deployment (parity carry-forward). `agentship serve` doctor-gates
# the agents dir + auth provider, then runs uvicorn. $PORT is provided by the platform;
# $WEB_CONCURRENCY (if set) selects worker processes.
web: agentship serve --host 0.0.0.0 --port ${PORT:-8000} --agents-dir ${AGENTSHIP_AGENTS_DIR:-agents} --auth ${AGENTSHIP_AUTH_PROVIDER:-api_key} --workers ${WEB_CONCURRENCY:-1}
