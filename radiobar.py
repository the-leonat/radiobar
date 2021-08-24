#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import vlc
from Cocoa import (NSFont, NSFontAttributeName, NSColor,
                   NSForegroundColorAttributeName)
from PyObjCTools.Conversion import propertyListFromPythonCollection
from AppKit import NSAttributedString, NSScreen
import rumps
import ctypes
import os
import platform
import requests
import socket
import threading
import json
import time
import re
from getmeta import TitleListener
from datetime import datetime
rumps.debug_mode(True)


# preload libvlccore.dylib
# https://github.com/oaubert/python-vlc/issues/37
d = '/Applications/VLC.app/Contents/MacOS/'
p = d + 'lib/libvlc.dylib'
if os.path.exists(p):
    # force pre-load of libvlccore.dylib  # ****
    ctypes.CDLL(d + 'lib/libvlccore.dylib')  # ****
    dll = ctypes.CDLL(p)


if 'VLC_PLUGIN_PATH' not in os.environ:
    # print('VLC_PLUGIN_PATH not set. Setting now...')
    os.environ['VLC_PLUGIN_PATH'] = '$VLC_PLUGIN_PATH:/Applications/VLC.app/Contents/MacOS/plugins'


def rpart(s):
    if len(s) > 40:
        part = s.rpartition(' ')
        return part[-1] + part[1] + part[0]
    return s

class RadioBarRemoteThread(threading.Thread):
    def __init__(self, radiobar, host, port):
        super(RadioBarRemoteThread, self).__init__()
        self.stop_event = threading.Event()

        self.radiobar = radiobar

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.host = host
        self.port = port
        self.socket.bind((self.host, self.port))
        self.socket.listen()

    def run(self):
        radiobar = self.radiobar
        while not self.stop_event.is_set():
            c, addr = self.socket.accept()
            data = c.recv(1024)
            msg = data.decode("utf-8")
            print("Remote received: " + msg)
            if msg == "":
                radiobar.toggle(radiobar.menu[radiobar.active_station])
            elif msg.isnumeric() and 0 <= int(msg)-1 < len(radiobar.stations):
                radiobar.play(
                    radiobar.menu[radiobar.stations[int(msg)-1]['title']])
                c.send(b'Listening to ' +
                       radiobar.stations[int(msg)-1]['title'].encode('utf-8'))
            elif msg == "off":
                radiobar.stop(radiobar.menu["Stop"])
                c.send(b'Off')
            elif msg == "on" or msg == "resume":
                if radiobar.active_station:
                    radiobar.toggle(radiobar.menu[radiobar.active_station])
                    c.send(b'On')
            elif msg == "pause":
                if radiobar.active_station:
                    radiobar.toggle(radiobar.menu[radiobar.active_station])
                    c.send(b'Pause')
            elif msg == "nowplaying":
                c.send(bytes(radiobar.nowplaying.encode('utf-8')))
            elif msg == "show":
                radiobar.notify(radiobar.nowplaying)
                c.send(bytes(radiobar.nowplaying.encode('utf-8')))
            elif msg == "toggle":
                radiobar.toggle()
                c.send(b'Toggle ' + radiobar.active_station.encode('utf-8'))
            else:
                c.send(b'Unknown input')
        c.close()

    def stop(self):
        self.stop_event.set()


class RadioBar(rumps.App):

    def __init__(self):
        super(RadioBar, self).__init__('RadioBar',
                                       icon='radio-icon-grey.png', template=None, quit_button=None)

        self.show_notifications = True
        self.show_notification_station_change = False
        self.show_nowplaying_menubar = True
        # 'radio-icon.png' or 'radio-icon-green.png'
        self.default_icon = 'radio-icon.png'
        self.default_icon_disabled = 'radio-icon-grey.png'
        # format: r,g,b,alpha // [29,185,84,1] = "Spotify green" // [0,0,0,1] = "White"
        self.default_color_list = [255, 255, 255, 1]
        self.default_color_list_disabled = [255, 255, 255, 0.4]
        # Truncate if the screen is smaller than 1440px wide
        self.truncate = NSScreen.screens()[0].frame().size.width <= 1440

        self.active_station = None
        self.nowplaying = None
        self.titleListener = TitleListener()
        self.vlcInstance = vlc.Instance()
        self.player = self.vlcInstance.media_player_new()
        self.media = None
        self.stations = []
        self.urls = {}
        self.get_stations()
        # prevent multiple calls from sleep/wake
        self.awake = True

        self.threads = []
        remote_thread = RadioBarRemoteThread(self, '127.0.0.1', 65432)
        remote_thread.daemon = True
        self.threads.append(remote_thread)
        remote_thread.start()

    def log(self, msg):
        print(msg)

    def set_title(self, title, color_list=None):
        self.title = title
        if color_list is None:
            color_list = self.default_color_list

        if title is not None:
            if self.truncate and len(title) > 40:
                title = title[:37] + '...'

            # This is hacky, but works
            # https://github.com/jaredks/rumps/issues/30
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                color_list[0]/255, color_list[1]/255, color_list[2]/255, color_list[3])
            font = NSFont.menuBarFontOfSize_(0)
            attributes = propertyListFromPythonCollection(
                {NSForegroundColorAttributeName: color, NSFontAttributeName: font}, conversionHelper=lambda x: x)
            string = NSAttributedString.alloc().initWithString_attributes_(' ' + title, attributes)
            self._nsapp.nsstatusitem.setAttributedTitle_(string)

    def build_menu(self):
        self.menu.clear()

        if len(self.stations) < 1:
            rumps.alert('No stations loaded.')

        new_menu = []

        new_menu.append(rumps.MenuItem('Now Playing', callback=None))
        new_menu.append(rumps.separator)

        for station in self.stations:
            item = rumps.MenuItem(station['title'], callback=self.toggle)
            new_menu.append(item)

        new_menu.append(rumps.separator)
        new_menu.append(rumps.MenuItem('Stop'))
        new_menu.append(rumps.separator)
        new_menu.append(rumps.MenuItem('Quit RadioBar', callback=self.quit))

        self.menu = new_menu
        self.menu['Now Playing'].title = 'Nothing playing...'

    def get_stations(self):
        if len(self.stations) > 0:
            return

        try:
            with open('channels.json') as json_data:
                j = json.load(json_data)
                for c in j['channels']:
                    self.stations.append(c)
                    self.urls[c['title']] = c['url']
        except requests.exceptions.RequestException as e:
            rumps.alert(e)

        self.build_menu()

    def start_radio(self):
        # craft station url
        station_url = self.urls[self.active_station]
        print(u'Playing URL %s' % station_url)
        # feed url to player
        self.media = self.vlcInstance.media_new_location(station_url)  # Your audio file here
        self.titleListener.listen(station_url)
        self.player.set_media(self.media)
        self.player.play()
        self.update_nowplaying()

    def reset_menu_state(self):
        if self.active_station is None:
            return
        self.menu[self.active_station].state = 0
        self.menu[self.active_station].set_callback(self.toggle)
        self.active_station = None
        self.set_title(None)
        self.menu['Stop'].state = 0
        self.menu['Stop'].title = 'Stop'
        self.menu['Stop'].set_callback(None)
        self.icon = self.default_icon_disabled
        self.menu['Now Playing'].title = 'Nothing playing...'

    def play(self, sender):
        # is there already a station playing or a paused station?
        if self.active_station is not None and self.menu[self.active_station].state != 0:
            self.reset_menu_state()

        self.active_station = sender.title
        self.set_title(sender.title)
        self.icon = self.default_icon
        sender.state = 1
        self.menu['Stop'].set_callback(self.stop)
        self.menu['Stop'].title = 'Stop'

        print("Switching to station: " + self.active_station)
        self.start_radio()
        print ("updated radio")

        time.sleep(.5)
        self.update_nowplaying()


    def toggle(self, sender):
        # Stopped -> Playing
        if sender is not None:
            # Starting to play - not been playing before
            if self.active_station is None:
                self.play(sender)
            # Paused, but we want to play another station
            elif self.menu[self.active_station] is not sender:
                self.play(sender)
            # Paused and clicked the currently paused station
            else:
                active_menu = self.menu[self.active_station]
                # Playing -> Paused
                if active_menu.state == 1:
                    self.pause(active_menu)
                # Paused -> Playing
                elif active_menu.state == -1:
                    self.play(active_menu)

    def pause(self, sender):
        # We're really stopping (because it's live radio and we don't want to buffer)
        self.player.stop()
        sender.state = - 1
        self.set_title(self.active_station, self.default_color_list_disabled)
        self.icon = self.default_icon_disabled
        self.nowplaying = None
        self.menu['Now Playing'].title = 'Paused'

    def stop(self, sender):
        self.player.stop()
        self.reset_menu_state()
        self.notify("Stopped")

    def get_nowplaying(self):
        if self.active_station is not None:
            return self.titleListener.get_current_showtitle()

    def update_nowplaying(self):
        state = self.player.get_state()
        # Try to update information asap, even if vlc.State.Opening
        if self.active_station is not None:
            old_info = self.nowplaying
            new_info = self.get_nowplaying()
            if (new_info != old_info):
                self.menu['Now Playing'].title = new_info

            # if old_info is None or new_info != old_info:
            #     # This depends on how your stations work, but for me the station changes back to "Station Name - Show Name" after a song
            #     # and I don't want notifications all the time the show name comes back on as Now Playing new_info...
            #     # So we only show notifications when the new info doesn't start with the station name.
            #     if not new.startswith(self.active_station):
            #         self.notify(new_info)

    @rumps.timer(5)
    def track_metadata_changes(self, sender):
        self.update_nowplaying()

    def notify(self, msg):
        print("Notification: " + msg)
        if self.active_station:
            rumps.notification('RadioBar', self.active_station, msg)
        else:
            rumps.notification('RadioBar', msg, None)

    def quit(self, sender):
        for t in self.threads:
            t.stop()
        self.titleListener.quit()
        rumps.quit_application(sender)

    def sleep(self):
        print("Going to sleep!")
        if self.awake and self.active_station:
            self.pause(self.menu[self.active_station])
        self.awake = False

    def wake(self):
        print("Waking up!")
        self.awake = True


if __name__ == "__main__":
    RadioBar().run()
