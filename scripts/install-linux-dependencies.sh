#!/bin/sh
set -eu
# Ubuntu 22.04+ / Debian. Other distributions: see README equivalents.
sudo apt-get update
audio_package=libasound2
if apt-cache show libasound2t64 >/dev/null 2>&1; then audio_package=libasound2t64; fi
sudo apt-get install -y ffmpeg zenity libegl1 libopengl0 libxcb-xinerama0 libxcb-cursor0 libxcb-keysyms1 libxcb-icccm4 libxkbcommon-x11-0 libnss3 "$audio_package" libvulkan1 mesa-vulkan-drivers libsecret-1-0
