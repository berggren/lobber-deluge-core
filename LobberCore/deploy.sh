rm -f /home/jbn/.config/deluge/plugins/LobberCore-0.1-py2.7.egg
python setup.py bdist_egg 1>/dev/null
cp dist/LobberCore-0.1-py2.7.egg /home/jbn/.config/deluge/plugins
