
import time
import asyncio
import json
from logs import logger

import aiohttp
import requests
from config import *
from decimal import Decimal

from sys import exc_info
from traceback import extract_tb

# HTTP and WebSocket endpoints
host = "http://localhost:8843"
ws_host = host.replace("http", "ws")

# Reusable aiohttp session for HTTP and WebSocket requests
_session = None


async def _get_session():
    """Create or return the module-level aiohttp.ClientSession."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    """Close the module-level aiohttp session."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()


async def _request_json(url):
    session = await _get_session()
    async with session.get(url) as resp:
        return await resp.json()


async def wallet_and_private_key():
    session = await _get_session()
    async with session.get(host + f"/unsf/get_wallet?mnemonic={mnemonic}") as resp:
        res = await resp.json()
    if "result" in res:
        return res["result"]["Address"], res["result"]["PrivateKey"]
    print("Укажите данные от счета..")
    return None, None


wallet, priv_key_minter = asyncio.run(wallet_and_private_key())
ten_in_18 = Decimal("10") ** Decimal("18")

def format_e(x):
    if "e-" in str(x):
        number = f'{x:.{str(x).split("-")[1]}f}'
        number = number.split(".")[0]
        return number
    elif "e+" in str(x):
        number = f'{x:.{str(x).split("+")[1]}f}'
        number = number.split(".")[0]
        return number

    if "." in str(x) and int(x) == float(x): return int(x)
    return x

async def get_transaction_async(tx):
    url = f"https://explorer-api.minter.network/api/v2/transactions/{tx}"
    return await _request_json(url)

def get_transaction(tx):
    return asyncio.run(get_transaction_async(tx))

async def send_transaction_async(transaction, gas_price=None, gas_coin=None):
    url = host + f"/hamster/send_transaction/{transaction}?payload={message_transaction}"
    if gas_price is None:
        gas_price = globals().get("gas_price", None)
    if gas_price is not None:
        url += f"&gas_price={gas_price}"
    if gas_coin:
        url += f"&gas_coin={gas_coin}"
    res = await _request_json(url)

    if "result" not in res or res["result"].get("code", 0) != 0:
        msg = ""
        if "Wanted" in res["error"]:
            ws = res["error"].split(" ")
            for i, w in enumerate(ws):
                if w == "Wanted" and i + 2 <= len(ws) - 1:
                    s = ws[i + 1]
                    c = ws[i + 2]

                    msg = f"Недостаточно средств на счете {Decimal(s) / ten_in_18} {c}"

        raise Exception(f"{msg} {res}")

    return res["result"]["hash"]

def send_transaction(transaction, gas_price=None, gas_coin=None):
    return asyncio.run(send_transaction_async(transaction, gas_price, gas_coin))

def order_format(symbol, order):
    try:
        side = "long" if symbol.split("/")[1] == order["coin_to_sell"]["symbol"] else "short"
        price = float(order["initial_coin_to_buy_volume"]) / float(order["initial_coin_to_sell_volume"]) if side == "short" else float(order["initial_coin_to_sell_volume"]) / float(order["initial_coin_to_buy_volume"])
        return {
            "orderId": order["id"],
            "symbol": symbol,
            "side": side,
            "size": float(order["initial_coin_to_buy_volume"]) if side == "long" else float(order["initial_coin_to_buy_volume"]) / price,
            "price": price,
            "status": order["status"]
        }
    except Exception as err:
        print(order, err, extract_tb(exc_info()[2]))

def send_order(symbol, transaction):
    tx = send_transaction(transaction)
    time.sleep(1)
    for i in range(30):
        transaction = get_transaction(tx)
        if "data" in transaction:
            transaction = transaction["data"]
            type_order = "limit" if "order_id" in transaction["data"] else "market"

            if type_order == "limit":
                side = "long" if symbol.split("/")[1] == transaction["data"]["coin_to_sell"]["symbol"] else "short"
                return {
                    "orderId": transaction["data"]["order_id"],
                    "symbol": symbol,
                    "side": side,
                    "size": float(transaction["data"]["value_to_buy"]) if side == "long" else float(transaction["data"]["value_to_sell"]),
                    "price": float(transaction["data"]["value_to_sell"]) / float(transaction["data"]["value_to_buy"]) if side == "long" else float(transaction["data"]["value_to_buy"]) / float(transaction["data"]["value_to_sell"])
                }
            elif type_order == "market":
                side = "long" if symbol.split("/")[1] == transaction["data"]["coins"][0]["symbol"] else "short"
                logger.info(symbol, transaction)

                return {
                    "orderId": "market",
                    "symbol": symbol,
                    "side": side,
                    "size": float(transaction["data"]["value_to_buy"]) if side == "long" else float(transaction["data"]["value_to_sell"]),
                    "price": float(transaction["data"]["value_to_sell"]) / float(transaction["data"]["value_to_buy"]) if side == "long" else float(transaction["data"]["value_to_buy"]) / float(transaction["data"]["value_to_sell"])
                }
        time.sleep(3)

def get_balance(wallet):
    res = requests.get(host + f"/hamster/get_balance/{wallet}?fields=freefloat").json()
    balances = {}
    for r in res["result"]["freefloat"]:
        balances[r["symbol"]] = float(r["volume"]) / (10**18)

    return balances

def get_coins_id():
    res = requests.get(host + "/hamster/get_pools").json()["result"]
    coins_id = {}

    for r in res:
        s1 = r["coin0"]["symbol"]
        s2 = r["coin1"]["symbol"]

        if "coin2" in r:
            pass
        if s1 not in coins_id: coins_id[s1] = r["coin0"]["id"]
        if s2 not in coins_id: coins_id[s2] = r["coin1"]["id"]

    return coins_id

coins_id = get_coins_id()

def _parse_pools(res):
    pools = {}
    for r in res:
        symbol1 = f'{r["coin0"]["symbol"]}/{r["coin1"]["symbol"]}'
        symbol2 = f'{r["coin1"]["symbol"]}/{r["coin0"]["symbol"]}'

        size0 = Decimal(r["coin0"]["reserve"]) / ten_in_18
        size1 = Decimal(r["coin1"]["reserve"]) / ten_in_18

        pools[symbol1] = {
            "price": size1 / size0,
            "size0": size0,
            "size1": size1,
        }

        pools[symbol2] = {
            "price": size0 / size1,
            "size0": size1,
            "size1": size0,
        }

    return pools

async def get_pools_async():
    res = await _request_json(host + "/hamster/get_pools")
    res = res["result"]
    return _parse_pools(res)

def get_pools():
    return asyncio.run(get_pools_async())


def get_exchange_information():
    """Return simplified exchange information based on available pools."""
    res = requests.get(host + "/hamster/get_pools").json()["result"]

    symbols = []
    for r in res:
        base = r["coin0"]["symbol"]
        quote = r["coin1"]["symbol"]

        common_filters = [
            {"filterType": "LOT_SIZE", "minQty": "1"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.00000001"},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "1"},
        ]

        symbols.append({
            "symbol": f"{base}/{quote}",
            "status": "TRADING",
            "baseAsset": base,
            "quoteAsset": quote,
            "filters": common_filters,
        })

        symbols.append({
            "symbol": f"{quote}/{base}",
            "status": "TRADING",
            "baseAsset": quote,
            "quoteAsset": base,
            "filters": common_filters,
        })

    return {"symbols": symbols}

async def pools_ws():
    url = ws_host + "/hamster/pools/ws"
    while True:
        try:
            session = await _get_session()
            async with session.ws_connect(url) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        res = data.get("result", data)
                        yield _parse_pools(res)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        except Exception as err:
            logger.error([err])
            await asyncio.sleep(1)

def get_price(symbol, size=0, type="output"):
    if size != 0:
        size = int(Decimal(size) * ten_in_18)
        res = requests.get(f"https://explorer-api.minter.network/api/v2/pools/coins/{symbol.split('/')[1]}/{symbol.split('/')[0]}/route?type={type}&amount={size}").json()
        if "amount_in" in res:
            return Decimal(float(res["amount_in"])) / Decimal(float(res["amount_out"]))
        else:
            return None
    else:
        res = requests.get(host + f"/hamster/get_pool/{symbol}")
        return float(res.json()["result"]["price"])

def get_order(symbol, orderId):
    orders = get_orders(symbol)
    for o in orders:
        if orderId == o["orderId"]:
            return o

def get_orders(symbol, status=""):
    orders = []
    res = requests.get(f"https://explorer-api.minter.network/api/v2/addresses/{wallet}/orders?active_tab=order").json()["data"]
    if status:
        orders = []
        for o in res:
            if o["status"] == status:
                orders.append(order_format(symbol, o))
    else:
        for r in res:
            orders.append(order_format(symbol, r))
    return orders

def get_open_orders(symbol):
    res = requests.get(f"https://explorer-api.minter.network/api/v2/addresses/{wallet}/orders?active_tab=order").json()["result"]
    return res

def new_wallet():
    res = requests.get(host + f"/unsf/new_wallet").json()
    return res

def get_route_input(symbol, size):
    res = requests.get(host + f"/hamster/get_best_trade/{symbol}/input/{size}").json()
    res["result"]["path"] = str(res["result"]["path"]).replace(" ", "").replace("[", "").replace("]", "")
    return res["result"]

def get_route_output(symbol, size):
    res = requests.get(host + f"/hamster/get_best_trade/{symbol}/output/{size}").json()
    res["result"]["path"] = str(res["result"]["path"]).replace(" ", "").replace("[", "").replace("]", "")
    return res["result"]

def buy_market(symbol, size):
    min_value_buy = int(Decimal(size) * Decimal(get_price(symbol, size)) * ten_in_18)
    size = int(Decimal(size) * ten_in_18)

    route1 = get_route_input(f"{symbol.split('/')[1]}/{symbol.split('/')[0]}", min_value_buy)
    route2 = get_route_output(f"{symbol.split('/')[1]}/{symbol.split('/')[0]}", route1["result"])
    size = route1["result"]

    params = f"/unsf/new_tx/sell_swap_pool?" \
             f"priv_key={priv_key_minter}" \
             f"&value_sell={min_value_buy}" \
             f"&min_value_buy={size}" \
             f"&route={route1['path']}"

    transaction = requests.get(host + params)
    logger.info(f"{symbol} buy market {host + params}")

    return send_order(symbol, transaction.json()['result'])

def sell_market(symbol, size):
    min_value_buy = int(Decimal(size) * Decimal(get_price(symbol, size)) * ten_in_18)
    size = int(Decimal(size) * ten_in_18)

    route1 = get_route_input(f"{symbol.split('/')[0]}/{symbol.split('/')[1]}", size)
    route2 = get_route_output(f"{symbol.split('/')[0]}/{symbol.split('/')[1]}", route1["result"])
    min_value_buy = route1["result"]

    params = f"/unsf/new_tx/sell_swap_pool?" \
             f"priv_key={priv_key_minter}" \
             f"&value_sell={size}" \
             f"&min_value_buy={min_value_buy}" \
             f"&route={route2['path']}"

    transaction = requests.get(host + params)

    logger.info(f"{symbol} sell market {host + params}")
    return send_order(symbol, transaction.json()['result'])

def buy_limit(symbol, size, price):
    value_sell = int(Decimal(size) * Decimal(price) * ten_in_18)
    value_buy = int(Decimal(size) * ten_in_18)

    transaction = requests.get(
        host + f"/unsf/new_tx/add_limit_order?"
        f"priv_key={priv_key_minter}"
        f"&coin_sell={symbol.split('/')[1]}"
        f"&coin_buy={symbol.split('/')[0]}"
        f"&value_buy={value_buy}"
        f"&value_sell={value_sell}")

    return send_order(symbol, transaction.json()['result'])

def sell_limit(symbol, size, price):
    value_sell = int(Decimal(size) * ten_in_18)
    value_buy = int(Decimal(size) * Decimal(price) * ten_in_18)

    transaction = requests.get(
        host + f"/unsf/new_tx/add_limit_order?"
        f"priv_key={priv_key_minter}"
        f"&coin_sell={symbol.split('/')[0]}"
        f"&coin_buy={symbol.split('/')[1]}"
        f"&value_buy={value_buy}"
        f"&value_sell={value_sell}")

    return send_order(symbol, transaction.json()['result'])

def cancel_order(orderId):
    try:
        transaction = requests.get(host + f"/unsf/new_tx/remove_limit_order?"
                           f"priv_key={priv_key_minter}"
                           f"&id={orderId}")

        return send_transaction(transaction.json()['result'])
    except Exception as e:
        logger.error(e)

async def swap_pool_async(path, size, gas_price=None):
    size = int(Decimal(size) * ten_in_18)

    url = (host + f"/unsf/new_tx/sell_swap_pool?"
           f"priv_key={priv_key_minter}"
           f"&value_sell={size}"
           f"&min_value_buy=1"
           f"&route={path}")

    transaction = await _request_json(url)

    return await send_transaction_async(transaction['result'], gas_price=gas_price)

def swap_pool(path, size, gas_price=None):
    return asyncio.run(swap_pool_async(path, size, gas_price))

def get_path(coins):
    coins = coins.replace(" ", "").split(">")
    path = ""
    for coin in coins:
        path += f"{coins_id[coin]},"

    return path[:-1]
  
if __name__ == '__main__':
    pass
