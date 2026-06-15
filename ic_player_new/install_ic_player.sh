#!/bin/bash
# check if required binaries are installed

required_tools=("git", "podman")
for tool_index in "${!required_tools[@]}"; do
    tool_name="${required_tools[$tool_index]}"
    if ! command -v "$tool_name" &> /dev/null; then
        apt install "$tool_name"
    fi
done

current_directory=$(pwd)
default_browser=$(xdg-settings get default-web-browser)
default_browser_binary=$(sed 's/.desktop//' <<< "$default_browser")
/usr/bin/"$default_browser_binary" "http://localhost:8000"
