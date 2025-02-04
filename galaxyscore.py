#!/usr/bin/env python3
"""Cyberjunky's 3Commas bot helpers."""
import argparse
import configparser
import json
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import TimedRotatingFileHandler as _TimedRotatingFileHandler
from pathlib import Path

import apprise
import requests
from py3cw.request import Py3CW


class NotificationHandler:
    """Notification class."""

    def __init__(self, enabled=False, notify_urls=None):
        if enabled and notify_urls:
            self.apobj = apprise.Apprise()
            urls = json.loads(notify_urls)
            for url in urls:
                self.apobj.add(url)
            self.queue = queue.Queue()
            self.start_worker()
            self.enabled = True
        else:
            self.enabled = False

    def start_worker(self):
        """Start notification worker."""
        threading.Thread(target=self.process_queue, daemon=True).start()

    def process_queue(self):
        """Process the queue."""
        while True:
            message, attachments = self.queue.get()
            if attachments:
                self.apobj.notify(body=message, attach=attachments)
            else:
                self.apobj.notify(body=message)
            self.queue.task_done()

    def send_notification(self, message, attachments=None):
        """Send a notification if enabled."""
        if self.enabled:
            msg = f"[3Commas bots helper {program}]\n" + message
            self.queue.put((msg, attachments or []))


class TimedRotatingFileHandler(_TimedRotatingFileHandler):
    """Override original code to fix bug with not deleting old logfiles."""

    def __init__(self, filename="", when="midnight", interval=1, backupCount=7):
        super().__init__(
            filename=filename,
            when=when,
            interval=int(interval),
            backupCount=int(backupCount),
        )

    def getFilesToDelete(self):
        """Find all logfiles present."""
        dirname, basename = os.path.split(self.baseFilename)
        filenames = os.listdir(dirname)
        result = []
        prefix = basename + "."
        plen = len(prefix)
        for filename in filenames:
            if filename[:plen] == prefix:
                suffix = filename[plen:]
                if self.extMatch.match(suffix):
                    result.append(os.path.join(dirname, filename))
        result.sort()
        if len(result) < self.backupCount:
            result = []
        else:
            result = result[: len(result) - self.backupCount]
        return result

    def doRollover(self):
        """Delete old logfiles but keep latest backupCount amount."""
        super().doRollover()
        self.close()
        timetuple = time.localtime(time.time())
        dfn = self.baseFilename + "." + time.strftime(self.suffix, timetuple)

        if os.path.exists(dfn):
            os.remove(dfn)

        os.rename(self.baseFilename, dfn)

        if self.backupCount > 0:
            for oldlog in self.getFilesToDelete():
                os.remove(oldlog)

        self.stream = open(self.baseFilename, "w")

        currenttime = int(time.time())
        newrolloverat = self.computeRollover(currenttime)
        while newrolloverat <= currenttime:
            newrolloverat = newrolloverat + self.interval

        self.rolloverAt = newrolloverat


class Logger:
    """Logger class."""

    my_logger = None

    def __init__(self, notificationhandler, logstokeep, debug_enabled, notify_enabled):
        """Logger init."""
        self.my_logger = logging.getLogger()
        self.notify_enabled = notify_enabled
        self.notificationhandler = notificationhandler
        if debug_enabled:
            self.my_logger.setLevel(logging.DEBUG)
            self.my_logger.propagate = True
        else:
            self.my_logger.setLevel(logging.INFO)
            self.my_logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # Create directory if not exists
        if not os.path.exists(f"{datadir}/logs"):
            os.makedirs(f"{datadir}/logs")

        # Log to file and rotate if needed
        file_handle = TimedRotatingFileHandler(
            filename=f"{datadir}/logs/{program}.log", backupCount=logstokeep
        )
        file_handle.setLevel(logging.DEBUG)
        file_handle.setFormatter(formatter)
        self.my_logger.addHandler(file_handle)

        # Log to console
        console_handle = logging.StreamHandler()
        console_handle.setLevel(logging.INFO)
        console_handle.setFormatter(formatter)
        self.my_logger.addHandler(console_handle)

    def log(self, message, level="info"):
        """Call the log levels."""
        if level == "info":
            self.my_logger.info(message)
        elif level == "warning":
            self.my_logger.warning(message)
        elif level == "error":
            self.my_logger.error(message)
        elif level == "debug":
            self.my_logger.debug(message)

    def info(self, message, notify=False):
        """Info level."""
        self.log(message, "info")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)

    def warning(self, message, notify=True):
        """Warning level."""
        self.log(message, "warning")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)

    def error(self, message, notify=True):
        """Error level."""
        self.log(message, "error")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)

    def debug(self, message, notify=False):
        """Debug level."""
        self.log(message, "debug")
        if self.notify_enabled and notify:
            self.notificationhandler.send_notification(message)


def load_config():
    """Create default or load existing config file."""

    cfg = configparser.ConfigParser()
    if cfg.read(f"{datadir}/{program}.ini"):
        return cfg

    cfg["settings"] = {
        "timezone": "Europe/Amsterdam",
        "timeinterval": 3600,
        "debug": False,
        "logrotate": 7,
        "botids": [12345, 67890],
        "numberofpairs": 10,
        "accountmode": "paper",
        "3c-apikey": "Your 3Commas API Key",
        "3c-apisecret": "Your 3Commas API Secret",
        "lc-apikey": "Your LunarCrush API Key",
        "notifications": False,
        "notify-urls": ["notify-url1"],
    }

    with open(f"{datadir}/{program}.ini", "w") as cfgfile:
        cfg.write(cfgfile)

    return None


def init_threecommas_api(cfg):
    """Init the 3commas API."""
    return Py3CW(
        key=cfg.get("settings", "3c-apikey"),
        secret=cfg.get("settings", "3c-apisecret"),
        request_options={
            "request_timeout": 10,
            "nr_of_retries": 3,
            "retry_status_codes": [502],
        },
    )


def get_filebased_blacklist():
    """Get the pair blacklist from local file."""

    newblacklist = []
    try:
        with open(blacklistfile, "r") as file:
            newblacklist = file.readlines()
        if newblacklist:
            logger.info(
                "Reading local blacklist file '%s' OK (%s pairs)"
                % (blacklistfile, len(newblacklist))
            )
    except FileNotFoundError:
        logger.error(
            "Reading local blacklist file '%s' failed with error: File not found"
            % blacklistfile
        )

    return newblacklist


def get_threecommas_blacklist():
    """Get the pair blacklist from 3Commas."""

    newblacklist = list()
    error, data = api.request(
        entity="bots",
        action="pairs_black_list",
        additional_headers={"Forced-Mode": MODE},
    )
    if data:
        logger.info(
            "Fetched 3Commas pairs blacklist OK (%s pairs)" % len(data["pairs"])
        )
        newblacklist = data["pairs"]
    else:
        logger.error(
            "Fetching 3Commas pairs blacklist failed with error: %s" % error["msg"]
        )

    return newblacklist


def get_threecommas_btcusd():
    """Get current USDT_BTC value to calculate BTC volume24h in USDT."""

    price = 60000
    error, data = api.request(
        entity="accounts",
        action="currency_rates",
        payload={"market_code": "binance", "pair": "USDT_BTC"},
        additional_headers={"Forced-Mode": MODE},
    )
    if data:
        logger.info("Fetched 3Commas BTC price in USDT %s OK" % data["last"])
        price = data["last"]
    else:
        logger.error(
            "Fetching 3Commas BTC price in USDT failed with error: %s" % error["msg"]
        )

    logger.debug("Current price of BTC is %s USDT" % price)
    return price


def get_threecommas_market(market_code):
    """Get all the valid pairs for market_code from 3Commas account."""

    tickerlist = []
    error, data = api.request(
        entity="accounts",
        action="market_pairs",
        payload={"market_code": market_code},
        additional_headers={"Forced-Mode": MODE},
    )
    if data:
        tickerlist = data
        logger.info(
            "Fetched 3Commas market data for %s OK (%s pairs)"
            % (market_code, len(tickerlist))
        )
    else:
        logger.error(
            "Fetching 3Commas market data failed with error: %s" % error["msg"]
        )

    return tickerlist


def get_lunarcrush_data():
    """Get the top x GalaxyScore or AltRank from LunarCrush."""

    scoredict = {}
    # Get current USD value of BTC
    usdtbtc = get_threecommas_btcusd()

    # Construct query for LunarCrush data
    if "altrank" in program:
        parms = {
            "data": "market",
            "type": "fast",
            "sort": "acr",
            "limit": 75,
            "key": config.get("settings", "lc-apikey"),
        }
    else:
        parms = {
            "data": "market",
            "type": "fast",
            "sort": "gs",
            "limit": 75,
            "key": config.get("settings", "lc-apikey"),
            "desc": True,
        }

    try:
        result = requests.get("https://api.lunarcrush.com/v2", params=parms)
        result.raise_for_status()
        data = result.json()
        if "data" in data.keys():
            for i, crush in enumerate(data["data"], start=1):
                crush["categories"] = (
                    list(crush["categories"].split(",")) if crush["categories"] else []
                )
                crush["rank"] = i
                crush["volbtc"] = crush["v"] / float(usdtbtc)
                logger.debug(
                    f"rank:{crush['rank']:3d}  acr:{crush['acr']:4d}   gs:{crush['gs']:3.1f}   "
                    f"s:{crush['s']:8s} '{crush['n']:25}'   volume in btc:{crush['volbtc']:12.2f}"
                    f"   categories:{crush['categories']}"
                )
            scoredict = data["data"]

    except requests.exceptions.HTTPError as err:
        logger.error("Fetching LunarCrush data failed with error: %s" % err)
        return None

    logger.info("Fetched LunarCrush ranking OK (%s coins)" % (len(scoredict)))

    return scoredict


def load_tickerlist(exchange):
    """Return tickerlist for exchange."""

    if "Binance" in exchange or "Paper Account" in exchange or MODE == "paper":
        return get_threecommas_market("binance")

    if exchange == "FTX":
        return get_threecommas_market("ftx")

    logger.error(
        "Bot is using the '%s' exchange which is not implemented yet!" % exchange
    )
    sys.exit()


def load_blacklist():
    """Return blacklist to be used."""

    if blacklistfile:
        return get_filebased_blacklist()

    return get_threecommas_blacklist()


def find_pairs(thebot):
    """Find new pairs and update the bot."""
    newpairslist = list()
    badpairslist = list()
    blackpairslist = list()

    # Update the blacklist
    blacklist = load_blacklist()

    # Gather bot settings
    base = thebot["pairs"][0].split("_")[0]
    exchange = thebot["account_name"]
    minvolume = thebot["min_volume_btc_24h"]

    logger.debug("Base currency for this bot: %s" % base)
    logger.debug("Exchange used by this bot: %s" % exchange)
    logger.debug("Minimal 24h BTC volume for this bot: %s" % minvolume)

    # Load tickerlist for exchange
    tickerlist = load_tickerlist(exchange)

    # Fetch and parse LunaCrush data
    for entry in get_lunarcrush_data():
        try:
            pair = base + "_" + entry["s"]
            volbtc = entry["volbtc"]

            # Check for valid data
            if volbtc is None or minvolume is None:
                logger.debug(
                    "Could not check 24h BTC volume for quote '%s', data is missing, skipping"
                    % entry["s"]
                )
                continue

            # Check if coin has minimum 24h volume as set in bot
            if float(volbtc) < float(minvolume):
                logger.debug(
                    "Quote currency '%s' does not have enough 24h BTC volume (%s), skipping"
                    % (entry["s"], str(volbtc))
                )
                continue

            # Check if pair is on 3Commas blacklist
            if pair in tickerlist:
                if pair in blacklist:
                    blackpairslist.append(pair)
                else:
                    newpairslist.append(pair)
            else:
                badpairslist.append(pair)

            # Did we get enough pairs already?
            fixednumpairs = int(config.get("settings", "numberofpairs"))
            if fixednumpairs:
                if len(newpairslist) == fixednumpairs:
                    break
            else:
                if len(newpairslist) == int(thebot["max_active_deals"]):
                    break

        except KeyError as err:
            logger.error(
                "Something went wrong while parsing LunarCrush data. KeyError for field %s"
                % err
            )
            return

    logger.debug(
        "These pairs are on your blacklist and were skipped: %s" % blackpairslist
    )

    logger.debug(
        "These pairs are invalid on the '%s' market according to 3Commas and were skipped: %s"
        % (exchange, badpairslist)
    )

    logger.debug("Current pairs: %s\nNew pairs: %s" % (thebot["pairs"], newpairslist))

    # Do we already use these pairs?
    if newpairslist == thebot["pairs"]:
        logger.info(
            "Bot '%s' with id '%s' is already using the best pairs"
            % (thebot["name"], thebot["id"]),
            True,
        )
        return

    # We have new pairs for our bot update it
    if not newpairslist:
        logger.info(
            "None of the by LunarCrush suggested pairs found on %s exchange!" % exchange
        )
        return

    update_bot(thebot, newpairslist)


def update_bot(thebot, newpairs):
    """Update bot with new pairs."""
    error, data = api.request(
        entity="bots",
        action="update",
        action_id=str(thebot["id"]),
        additional_headers={"Forced-Mode": MODE},
        payload={
            "name": str(thebot["name"]),
            "pairs": newpairs,
            "base_order_volume": float(thebot["base_order_volume"]),
            "take_profit": float(thebot["take_profit"]),
            "safety_order_volume": float(thebot["safety_order_volume"]),
            "martingale_volume_coefficient": float(
                thebot["martingale_volume_coefficient"]
            ),
            "martingale_step_coefficient": float(thebot["martingale_step_coefficient"]),
            "max_safety_orders": int(thebot["max_safety_orders"]),
            "max_active_deals": int(thebot["max_active_deals"]),
            "active_safety_orders_count": int(thebot["active_safety_orders_count"]),
            "safety_order_step_percentage": float(
                thebot["safety_order_step_percentage"]
            ),
            "take_profit_type": thebot["take_profit_type"],
            "strategy_list": thebot["strategy_list"],
            "bot_id": int(thebot["id"]),
        },
    )
    if data:
        logger.debug("Bot updated: %s" % data)
        logger.info(
            "Bot '%s' with id '%s' updated with these pairs:\n%s"
            % (thebot["name"], thebot["id"], newpairs),
            True,
        )
    else:
        logger.error(
            "Error occurred while updating bot '%s' error: %s"
            % (thebot["name"], error["msg"]),
            True,
        )


# Start application
program = Path(__file__).stem

# Parse and interpret options.
parser = argparse.ArgumentParser(description="Cyberjunky's 3Commas bot helper.")
parser.add_argument(
    "-d", "--datadir", help="directory to use for config and logs files", type=str
)
parser.add_argument(
    "-b", "--blacklist", help="local blacklist to use instead of 3Commas's", type=str
)

args = parser.parse_args()
if args.datadir:
    datadir = args.datadir
else:
    datadir = os.getcwd()

# pylint: disable-msg=C0103
if args.blacklist:
    blacklistfile = args.blacklist
else:
    blacklistfile = None

# Create or load configuration file
config = load_config()

if not config:
    logger = Logger(None, 7, False, False)
    logger.info(f"3Commas bot helper {program}!")
    logger.info("Started at %s." % time.strftime("%A %H:%M:%S %d-%m-%Y"))
    logger.info(
        f"Created example config file '{program}.ini', edit it and restart the program."
    )
    sys.exit(0)
else:
    # Handle timezone
    os.environ["TZ"] = config.get("settings", "timezone", fallback="Europe/Amsterdam")
    time.tzset()

    # Init notification handler
    notification = NotificationHandler(
        config.getboolean("settings", "notifications"),
        config.get("settings", "notify-urls"),
    )

    # Init logging
    logger = Logger(
        notification,
        int(config.get("settings", "logrotate", fallback=7)),
        config.getboolean("settings", "debug"),
        config.getboolean("settings", "notifications"),
    )
    logger.info(f"3Commas bot helper {program}")
    logger.info("Started at %s" % time.strftime("%A %H:%M:%S %d-%m-%Y"))
    logger.info(f"Loaded configuration from '{datadir}/{program}.ini'")

if config.get("settings", "accountmode") == "real":
    logger.info("Using REAL TRADING account")
    MODE = "real"
else:
    logger.info("Using PAPER TRADING account")
    MODE = "paper"

if notification.enabled:
    logger.info("Notifications are enabled")
else:
    logger.info("Notifications are disabled")

# Initialize 3Commas API
api = init_threecommas_api(config)

# Lunacrush GalayScore or AltRank pairs
while True:

    # Reload config files and refetch data to catch changes
    config = load_config()
    logger.info(f"Reloaded configuration from '{datadir}/{program}.ini'")
    botids = json.loads(config.get("settings", "botids"))

    # Walk through all bots specified
    for bot in botids:
        if bot == 0:
            continue

        boterror, botdata = api.request(
            entity="bots",
            action="show",
            action_id=str(bot),
            additional_headers={"Forced-Mode": MODE},
        )
        if botdata:
            find_pairs(botdata)
        else:
            logger.error("Error occurred updating bots: %s" % boterror["msg"])

    # pylint: disable=C0103
    timeint = int(config.get("settings", "timeinterval"))

    if timeint > 0:
        localtime = time.time()
        nexttime = localtime + int(timeint)
        timeresult = time.strftime("%H:%M:%S", time.localtime(nexttime))
        logger.info("Next update in %s Seconds at %s" % (timeint, timeresult), True)
        time.sleep(timeint)
    else:
        break
