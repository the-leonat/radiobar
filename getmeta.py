# taken from https://stackoverflow.com/questions/41022893/monitoring-icy-stream-metadata-title-python

from __future__ import unicode_literals

import re
import requests
import sys

try:
    from StringIO import StringIO as BytesIO
except ImportError:
    from io import BytesIO

import threading  # threading is better than the thread module
import queue


def icy_monitor(stream_url, change_callback=None, exit_callback=None):
    try:
        with requests.get(stream_url, headers={'Icy-MetaData': '1'}, stream=True, timeout=2) as r:
            if r.encoding is None:
                r.encoding = 'utf-8'

            byte_counter = 0
            meta_counter = 0
            metadata_buffer = BytesIO()

            if "icy-metaint" not in r.headers:
                print("no meta available")
                change_callback("No title info supported")
                return

            metadata_size = int(r.headers['icy-metaint']) + 255

            data_is_meta = False

            for byte in r.iter_content(1):
                if exit_callback():
                    print("Terminate thread for url {}...".format(stream_url))
                    return

                byte_counter += 1

                if (byte_counter <= 2048):
                    pass

                if (byte_counter > 2048):
                    if (meta_counter == 0):
                        meta_counter += 1

                    elif (meta_counter <= int(metadata_size + 1)):

                        metadata_buffer.write(byte)
                        meta_counter += 1
                    else:
                        data_is_meta = True

                if (byte_counter > 2048 + metadata_size):
                    byte_counter = 0

                if data_is_meta:
                    metadata_buffer.seek(0)

                    meta = metadata_buffer.read().rstrip(b'\0')

                    m = re.search(br"StreamTitle='([^']*)';", bytes(meta))
                    if m:
                        title = m.group(1).decode(r.encoding, errors='replace')
                        print('New title: {}'.format(title))

                        if change_callback:
                            change_callback(title)

                    byte_counter = 0
                    meta_counter = 0
                    metadata_buffer = BytesIO()

                    data_is_meta = False
    except:
        print("something failed")


def print_title(title):
    print('Title: {}'.format(title))


def run_in_thread(stream_url, queue, should_exit_cb):
    def on_showtitle_changed(title):
        queue.put(title)

    def should_exit():
        return should_exit_cb();

    icy_monitor(stream_url, change_callback=on_showtitle_changed,
                exit_callback=should_exit)


def apply_empty(title):
    if title == "" or len(title) <= 3:
        return "No title"
    if len(title) > 50:
        return title[:47] + '...'
    return title

class TitleListener():
    LOADING_LABEL = "Loading..."
    def __init__(self):
        self.title_queue = queue.Queue()  # use a queue to pass messages from the worker thread to the main thread
        self.current_stream_url = None;
        self.thread = None;
        self.should_exit_thread = False;
        self.last_showtitle = self.LOADING_LABEL;

    def get_current_showtitle(self):    
        while True:
            try:
                title = self.title_queue.get_nowait()
                # if title is none return last title available
                if title is None:
                    return apply_empty(self.last_showtitle)
                self.last_showtitle = title
                self.title_queue.task_done()
            except queue.Empty:
                return apply_empty(self.last_showtitle)

    def exit_thread(self):
        if self.thread is None:
            return
        # wait for the thread to terminate
        self.should_exit_thread = True
        self.thread.join()
        print ("closed thread")

    def start_thread(self, stream_url):
        def should_exit_thread():
            return self.should_exit_thread
        self.thread = threading.Thread(target=run_in_thread, args=(stream_url, self.title_queue, should_exit_thread))
        self.should_exit_thread = False
        self.last_showtitle = self.LOADING_LABEL
        self.thread.start();
        print("started thread for {}".format(stream_url))

    def quit(self):
        print ("quit")
        self.exit_thread()

    def listen(self, stream_url):
        if stream_url == self.current_stream_url:
            return
        
        self.current_stream_url = stream_url;
        # exit thread if exists
        self.exit_thread();
        # create new thread
        self.start_thread(stream_url);
    

if __name__ == '__main__':
    stream_url = sys.argv[1]
    icy_monitor(stream_url, callback=print_title)
