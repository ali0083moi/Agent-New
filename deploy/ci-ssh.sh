# Sourced by the GitHub Actions workflows: defines `ssh` (an array) and `target` from the DEPLOY_* secrets.
#   source deploy/ci-ssh.sh
#   "${ssh[@]}" "$target" 'uptime'
mkdir -p ~/.ssh && chmod 700 ~/.ssh
_opts=(-p "${DEPLOY_PORT:-22}" -o ConnectTimeout=30 -o ServerAliveInterval=20 -o ServerAliveCountMax=9)
if [ -n "${DEPLOY_KNOWN_HOSTS:-}" ]; then
  printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > ~/.ssh/known_hosts
  _opts+=(-o StrictHostKeyChecking=yes)
else
  _opts+=(-o StrictHostKeyChecking=accept-new)
fi
if [ -n "${DEPLOY_SSH_KEY:-}" ]; then
  printf '%s\n' "$DEPLOY_SSH_KEY" > ~/.ssh/deploy_key && chmod 600 ~/.ssh/deploy_key
  ssh=(ssh -i ~/.ssh/deploy_key -o IdentitiesOnly=yes "${_opts[@]}")
else
  command -v sshpass >/dev/null || sudo apt-get install -y -qq sshpass >/dev/null
  export SSHPASS="$DEPLOY_PASSWORD"
  ssh=(sshpass -e ssh -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no "${_opts[@]}")
fi
target="${DEPLOY_USER:-root}@$DEPLOY_HOST"
