#!/usr/bin/env bash

# ===== CONFIG =====
#PLUGINS_DIR="/path/to/your/server/plugins"
PLUGINS_DIR="/home/ximotu/azox-scripts" 
BASE_URL="https://repo.essentialsx.net/snapshots/net/essentialsx"

ARTIFACTS=(
  EssentialsX
  EssentialsXChat
  EssentialsXSpawn
  EssentialsXDiscord
  EssentialsXGeoIP
  EssentialsXProtect
  EssentialsXAntiBuild
)

# ==================

for A in "${ARTIFACTS[@]}"; do
  echo "=== Checking $A ==="

  # Get latest snapshot version (e.g. 2.22.0-SNAPSHOT)
  VER=$(curl -s "$BASE_URL/$A/maven-metadata.xml" \
    | sed -n 's:.*<latest>\(.*\)</latest>.*:\1:p')

  [[ -z "$VER" ]] && echo "Could not detect version" && continue

  META=$(curl -s "$BASE_URL/$A/$VER/maven-metadata.xml")

  TS=$(echo "$META" | sed -n 's:.*<timestamp>\(.*\)</timestamp>.*:\1:p')
  BN=$(echo "$META" | sed -n 's:.*<buildNumber>\(.*\)</buildNumber>.*:\1:p')

  [[ -z "$TS" || -z "$BN" ]] && echo "Could not detect build info" && continue

  BASE_VER="${VER/-SNAPSHOT/}"
  MAVEN_FILE="$A-$BASE_VER-$TS-$BN.jar"
  CLEAN_FILE="$A-$BASE_VER-$BN.jar"

  TARGET_FILE="$PLUGINS_DIR/$CLEAN_FILE"

  # Skip if already installed
  if [[ -f "$TARGET_FILE" ]]; then
    echo "Latest build already installed ($CLEAN_FILE)"
    echo
    continue
  fi

  echo "New build detected → $CLEAN_FILE"
  echo "Downloading..."

  curl -s -L -o "/tmp/$CLEAN_FILE" \
    "$BASE_URL/$A/$VER/$MAVEN_FILE"

  if [[ $? -ne 0 ]]; then
    echo "Download failed"
    continue
  fi

  # Remove older builds of same module
  echo "Removing old builds..."
  rm -f "$PLUGINS_DIR/$A-"*.jar

  # Move new file into plugins
  mv "/tmp/$CLEAN_FILE" "$PLUGINS_DIR/"

  echo "Installed $CLEAN_FILE"
  echo
done

echo "Update check complete."
