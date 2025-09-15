#!/bin/bash
DIR=/var/www/yvideo-py-dev

cd $DIR
source .venv/bin/activate
exec gunicorn -b localhost:5000 --env DJANGO_SETTINGS_MODULE=samltest.settings -w 4 --log-level 'debug' samltest.wsgi
