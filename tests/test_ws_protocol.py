"""Tests for scrap_pub.daemon.ws_protocol — encode/decode/helpers."""

import json

import pytest

from scrap_pub.daemon.ws_protocol import (
    CMD_ENQUEUE,
    CMD_LIST,
    CMD_STATUS,
    EVT_DAEMON_STATUS,
    decode,
    encode,
    reply_err,
    reply_ok,
)


def test_encode_produces_json_bytes():
    data = {"type": EVT_DAEMON_STATUS, "paused": False}
    result = encode(data)
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["type"] == EVT_DAEMON_STATUS


def test_decode_from_str():
    raw = json.dumps({"cmd": CMD_STATUS})
    msg = decode(raw)
    assert msg["cmd"] == CMD_STATUS


def test_decode_from_bytes():
    raw = json.dumps({"cmd": CMD_LIST}).encode()
    msg = decode(raw)
    assert msg["cmd"] == CMD_LIST


def test_decode_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        decode("not json at all")


def test_reply_ok_structure():
    reply = reply_ok(CMD_ENQUEUE, enqueued=3, task_ids=[1, 2, 3])
    assert reply["type"] == "reply"
    assert reply["cmd"]  == CMD_ENQUEUE
    assert reply["ok"]   is True
    assert reply["enqueued"] == 3
    assert reply["task_ids"] == [1, 2, 3]


def test_reply_err_structure():
    reply = reply_err(CMD_ENQUEUE, "missing 'url'")
    assert reply["type"]  == "reply"
    assert reply["cmd"]   == CMD_ENQUEUE
    assert reply["ok"]    is False
    assert reply["error"] == "missing 'url'"


def test_reply_ok_no_extra_kwargs():
    reply = reply_ok(CMD_STATUS)
    assert reply["ok"] is True
    assert "error" not in reply


def test_reply_err_no_extra_data():
    reply = reply_err(CMD_STATUS, "oops")
    assert reply["ok"] is False
    assert "enqueued" not in reply
