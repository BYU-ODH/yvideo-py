#!/bin/bash
# check if required binaries are installed
if ! command -v git &> /dev/null; then
	apt install git
fi

current_directory=$(pwd)
default_browser=$(xdg-settings get default-web-browser)
default_browser_binary=$(sed 's/.desktop//' <<< "$default_browser")
/usr/bin/"$default_browser_binary" "http://localhost:8000"
