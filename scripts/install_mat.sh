#!/usr/bin/env bash
# Install Eclipse MAT and the bundled JVMind query-service plugin.
#
# Usage:
#   ./scripts/install_mat.sh                # install to default MAT_HOME (/opt/mat)
#   ./scripts/install_mat.sh /path/to/mat   # install to a custom directory
#
# After installation, set MAT_HOME in .env and start the query-service:
#   MAT_QUERY_SERVICE_URL=http://127.0.0.1:8090
#   ./vendor/mat/ParseHeapDump.sh <hprof>   # one-shot parsing (worker)
#   java -jar ./vendor/mat/plugins/com.jvmind.mat.query-*.jar  # query-service
#
# The bundled query-service jar (com.jvmind.mat.query-*.jar) is shipped
# inside this repository under vendor/mat/. It implements the JSON over
# HTTP API consumed by app/routes/heapdump_proxy.py and react_agent/mat_tools.py.

set -euo pipefail

VERSION="1.17.0.20260601"
DEFAULT_HOME="/opt/mat"
MAT_HOME="${1:-${MAT_HOME:-$DEFAULT_HOME}}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLED_PLUGIN="$REPO_ROOT/vendor/mat/com.jvmind.mat.query-0.1.0.jar"

if [[ -t 1 ]]; then
  printf '\033[1;34mInstalling Eclipse MAT %s to %s\033[0m\n' "$VERSION" "$MAT_HOME"
fi

# 1. Download MAT if not present
mkdir -p "$MAT_HOME"
if [[ ! -x "$MAT_HOME/ParseHeapDump.sh" ]]; then
  if command -v wget >/dev/null 2>&1; then
    DL_CMD="wget -q"
  elif command -v curl >/dev/null 2>&1; then
    DL_CMD="curl -sSL -O"
  else
    echo "Need wget or curl to download MAT." >&2
    exit 1
  fi

  TMP="$(mktemp -d)"
  pushd "$TMP" >/dev/null
  URL="https://eclipse.dev/mat/downloads/snapshots/MemoryAnalyzer-${VERSION}-linux.gtk.x86_64.zip"
  echo "Downloading $URL ..."
  $DL_CMD "$URL"
  unzip -q "MemoryAnalyzer-${VERSION}-linux.gtk.x86_64.zip"
  # The zip unpacks into MemoryAnalyzer/ — copy its contents into MAT_HOME
  cp -r MemoryAnalyzer/. "$MAT_HOME/"
  popd >/dev/null
  rm -rf "$TMP"
  echo "MAT installed to $MAT_HOME"
else
  echo "MAT already present at $MAT_HOME"
fi

# 2. Copy the bundled query-service plugin into MAT's plugins/ directory
if [[ -f "$BUNDLED_PLUGIN" ]]; then
  cp "$BUNDLED_PLUGIN" "$MAT_HOME/plugins/"
  echo "Bundled query-service plugin copied to $MAT_HOME/plugins/"
else
  echo "WARN: bundled plugin not found at $BUNDLED_PLUGIN — heapdump query API disabled." >&2
fi

cat <<EOF

Done. To enable heapdump analysis:

  1. Set in .env:
       MAT_HOME=$MAT_HOME
       MAT_QUERY_SERVICE_URL=http://127.0.0.1:8090
  2. Start the query-service:
       $MAT_HOME/MemoryAnalyzer -consoleLog -nosplash -application com.jvmind.mat.query.QueryServiceApp
  3. Start the heapdump worker (separate process):
       jvmind-worker

EOF