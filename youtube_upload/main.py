#!/usr/bin/env python
#
# Upload videos to Youtube from the command-line using APIv3.
#
# Author: Arnau Sanchez <pyarnau@gmail.com>
# Project: https://github.com/tokland/youtube-upload
"""
Upload a video to Youtube from the command-line.

    $ youtube-upload --title="A.S. Mutter playing" \
                     --description="Anne Sophie Mutter plays Beethoven" \
                     --category=Music \
                     --tags="mutter, beethoven" \
                     anne_sophie_mutter.flv
    pxzZ-fYjeYs
"""

import os
import sys
import optparse
import collections
import webbrowser
from io import open

import googleapiclient.errors
import oauth2client

from oauth2client import file

from . import auth
from . import upload_video
from . import categories
from . import lib
from . import playlists

# http://code.google.com/p/python-progressbar (>= 2.3)
try:
    import progressbar
except ImportError:
    progressbar = None


class InvalidCategory(Exception): pass


class OptionsError(Exception): pass


class AuthenticationError(Exception): pass


class RequestError(Exception): pass


EXIT_CODES = {
    OptionsError: 2,
    InvalidCategory: 3,
    RequestError: 3,
    AuthenticationError: 4,
    oauth2client.client.FlowExchangeError: 4,
    NotImplementedError: 5,
}

WATCH_VIDEO_URL = "https://www.youtube.com/watch?v={id}"

debug = lib.debug
struct = collections.namedtuple


def open_link(url):
    """Opens a URL link in the client's browser."""
    webbrowser.open(url)


def get_progress_info():
    """Return a function callback to update the progressbar."""
    progressinfo = struct("ProgressInfo", ["callback", "finish"])

    if progressbar:
        bar = progressbar.ProgressBar(widgets=[
            progressbar.Percentage(),
            ' ', progressbar.Bar(),
            ' ', progressbar.FileTransferSpeed(),
            ' ', progressbar.DataSize(), '/', progressbar.DataSize('max_value'),
            ' ', progressbar.Timer(),
            ' ', progressbar.AdaptiveETA(),
        ])

        def _callback(total_size, completed):
            if not isinstance(bar.max_value, int):
                bar.max_value = total_size
                bar.start()
            else:
                bar.update(completed)

        def _finish():
            if not isinstance(bar.max_value, int):
                bar.max_value = 100
                bar.start()
            return bar.finish()

        return progressinfo(callback=_callback, finish=_finish)
    else:
        return progressinfo(callback=None, finish=lambda: True)


def get_category_id(category):
    """Return category ID from its name."""
    if category:
        if category in categories.IDS:
            return str(categories.IDS[category])
        else:
            msg = "{0} is not a valid category".format(category)
            raise InvalidCategory(msg)


def upload_youtube_video(youtube, options, video_path, total_videos, index, max_retries=1):
    """Upload video with index (for split videos)."""
    u = lib.to_utf8
    title = u(options.title)
    if hasattr(u('string'), 'decode'):
        description = u(options.description or "").decode("string-escape")
    else:
        description = options.description
    if options.publish_at:
        debug("Your video will remain private until specified date.")

    tags = [u(s.strip()) for s in (options.tags or "").split(",")]
    ns = dict(title=title, n=index + 1, total=total_videos)
    title_template = u(options.title_template)
    complete_title = (title_template.format(**ns) if total_videos > 1 else title)
    category_id = get_category_id(options.category)
    request_body = {
        "snippet": {
            "title": complete_title,
            "description": description,
            "categoryId": category_id,
            "tags": tags,
            "defaultLanguage": options.default_language,
            "defaultAudioLanguage": options.default_audio_language,

        },
        "status": {
            "embeddable": options.embeddable,
            "privacyStatus": ("private" if options.publish_at else options.privacy),
            "publishAt": options.publish_at,
            "license": options.license,

        },
        "recordingDetails": {
            "location": lib.string_to_dict(options.location),
            "recordingDate": options.recording_date,
        },
    }

    for i in range(max_retries):
        debug(f"Uploading try ({i+1}/{max_retries})...")
        try:
            progress = get_progress_info()
            video_id = upload_video.upload(
                youtube, video_path, request_body,
                progress_callback=progress.callback,
                chunksize=options.chunksize
            )
            progress.finish()
            return video_id
        except Exception as e:
            debug(f"An error occured while uploading the video: {e}.")
    return None


def get_youtube_handler(options):
    """Return the API Youtube object."""
    home = os.path.expanduser("~")
    default_credentials = os.path.join(home, ".youtube-upload-credentials.json")
    client_secrets = options.client_secrets or os.path.join(home, ".client_secrets.json")
    credentials = options.credentials_file or default_credentials
    get_code_callback = (auth.browser.get_code
                         if options.auth_browser else auth.console.get_code)
    return auth.get_resource(client_secrets, credentials,
                             get_code_callback=get_code_callback)


def parse_options_error(parser, options):
    """Check errors in options."""
    required_options = ["title"]
    missing = [opt for opt in required_options if not getattr(options, opt)]
    if missing:
        parser.print_usage()
        msg = "Some required option are missing: {0}".format(", ".join(missing))
        raise OptionsError(msg)


def run_main(parser, options, args, max_retries=1):
    """Run the main scripts from the parsed options/args."""
    parse_options_error(parser, options)
    youtube = get_youtube_handler(options)

    if youtube:
        for index, video_path in enumerate(args):
            video_id = upload_youtube_video(youtube, options, video_path, len(args), index, max_retries)
            video_url = WATCH_VIDEO_URL.format(id=video_id)
            if options.open_link:
                open_link(video_url)  # Opens the Youtube Video's link in a webbrowser

            if options.thumb:
                youtube.thumbnails().set(videoId=video_id, media_body=options.thumb).execute()
            if options.playlist:
                playlists.add_video_to_playlist(youtube, video_id,
                                                title=lib.to_utf8(options.playlist), privacy=options.privacy)
            return video_id
    else:
        raise AuthenticationError("Cannot get youtube resource")


def main(arguments):
    """Upload videos to Youtube."""
    usage = """Usage: %prog [OPTIONS] VIDEO [VIDEO2 ...]

    Upload videos to Youtube."""
    parser = optparse.OptionParser(usage)

    # Video metadata
    parser.add_option('-t', '--title', dest='title', type="string",
                      help='Video title')
    parser.add_option('-c', '--category', dest='category', type="string",
                      help='Name of video category')
    parser.add_option('-d', '--description', dest='description', type="string",
                      help='Video description')
    parser.add_option('', '--description-file', dest='description_file', type="string",
                      help='Video description file', default=None)
    parser.add_option('', '--tags', dest='tags', type="string",
                      help='Video tags (separated by commas: "tag1, tag2,...")')
    parser.add_option('', '--privacy', dest='privacy', metavar="STRING",
                      default="public", help='Privacy status (public | unlisted | private)')
    parser.add_option('', '--publish-at', dest='publish_at', metavar="datetime",
                      default=None, help='Publish date (ISO 8601): YYYY-MM-DDThh:mm:ss.sZ')
    parser.add_option('', '--max-retries', dest='max_retries', type="int",
                      default=1, help='Maximum number of retries (default: 1)')
    parser.add_option('', '--license', dest='license', metavar="string",
                      choices=('youtube', 'creativeCommon'), default='youtube',
                      help='License for the video, either "youtube" (the default) or "creativeCommon"')
    parser.add_option('', '--location', dest='location', type="string",
                      default=None, metavar="latitude=VAL,longitude=VAL[,altitude=VAL]",
                      help='Video location"')
    parser.add_option('', '--recording-date', dest='recording_date', metavar="datetime",
                      default=None, help="Recording date (ISO 8601): YYYY-MM-DDThh:mm:ss.sZ")
    parser.add_option('', '--default-language', dest='default_language', type="string",
                      default=None, metavar="string",
                      help="Default language (ISO 639-1: en | fr | de | ...)")
    parser.add_option('', '--default-audio-language', dest='default_audio_language', type="string",
                      default=None, metavar="string",
                      help="Default audio language (ISO 639-1: en | fr | de | ...)")
    parser.add_option('', '--thumbnail', dest='thumb', type="string", metavar="FILE",
                      help='Image file to use as video thumbnail (JPEG or PNG)')
    parser.add_option('', '--playlist', dest='playlist', type="string",
                      help='Playlist title (if it does not exist, it will be created)')
    parser.add_option('', '--title-template', dest='title_template',
                      type="string", default="{title} [{n}/{total}]", metavar="string",
                      help='Template for multiple videos (default: {title} [{n}/{total}])')
    parser.add_option('', '--embeddable', dest='embeddable', default=True,
                      help='Video is embeddable')

    # Authentication
    parser.add_option('', '--client-secrets', dest='client_secrets',
                      type="string", help='Client secrets JSON file')
    parser.add_option('', '--credentials-file', dest='credentials_file',
                      type="string", help='Credentials JSON file')
    parser.add_option('', '--auth-browser', dest='auth_browser', action='store_true',
                      help='Open a GUI browser to authenticate if required')

    # Additional options
    parser.add_option('', '--chunksize', dest='chunksize', type="int",
                      default=1024 * 1024 * 8, help='Update file chunksize')
    parser.add_option('', '--open-link', dest='open_link', action='store_true',
                      help='Opens a url in a web browser to display the uploaded video')

    options, args = parser.parse_args(arguments)

    if options.description_file is not None and os.path.exists(options.description_file):
        with open(options.description_file, encoding="utf-8") as file:
            options.description = file.read()

    try:
        return run_main(parser, options, args, max_retries=options.max_retries)
    except googleapiclient.errors.HttpError as error:
        response = bytes.decode(error.content, encoding=lib.get_encoding()).strip()
        raise RequestError(u"Server response: {0}".format(response))


def run():
    sys.exit(lib.catch_exceptions(EXIT_CODES, main, sys.argv[1:]))


if __name__ == '__main__':
    run()
