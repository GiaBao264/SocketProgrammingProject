"""
client_cache.py - simple client-side caching for VideoStreamProject_python

Features:
- JitterBuffer: orders incoming RTP packets and holds briefly to smooth bursts
- FrameAssembler: concatenates payloads with same timestamp into a frame
- ClientCache: provides on_rtp_packet(seq, payload, rtp_info) and get_frame()

This is intentionally simple to integrate with the existing project.
"""
import time, threading
from collections import OrderedDict, deque, namedtuple

Frame = namedtuple('Frame', ['timestamp', 'payload', 'seq_range', 'pts'])

class LRUCache:
    def __init__(self, max_items=300):
        self.max_items = max_items
        self._od = OrderedDict()
        self._lock = threading.Lock()
    def put(self, key, value):
        with self._lock:
            if key in self._od:
                self._od.move_to_end(key)
            self._od[key] = value
            while len(self._od) > self.max_items:
                self._od.popitem(last=False)
    def get(self, key, default=None):
        with self._lock:
            v = self._od.get(key, default)
            if v is not default:
                self._od.move_to_end(key)
            return v
    def pop(self, key, default=None):
        with self._lock:
            return self._od.pop(key, default)
    def __len__(self):
        with self._lock:
            return len(self._od)

class JitterBuffer:
    def __init__(self, max_hold_ms=150):
        self.buffer = {}   # seq -> (arrival_time, payload, rtp_info)
        self.lock = threading.Lock()
        self.max_hold = max_hold_ms / 1000.0
    def push_packet(self, seq, payload, rtp_info):
        t = time.time()
        with self.lock:
            self.buffer[seq] = (t, payload, rtp_info)
    def pop_ready_packets(self):
        now = time.time()
        ready = []
        with self.lock:
            if not self.buffer:
                return []
            seqs = sorted(self.buffer.keys())
            # deliver contiguous run from smallest seq
            run = []
            expected = seqs[0]
            for s in seqs:
                if s == expected:
                    run.append(s)
                    expected += 1
                else:
                    break
            if run:
                for s in run:
                    ready.append((s,) + self.buffer.pop(s)[1:])
                return ready
            # otherwise, if earliest packet old enough, deliver it
            earliest = seqs[0]
            arrival_time = self.buffer[earliest][0]
            if now - arrival_time >= self.max_hold:
                ready.append((earliest,) + self.buffer.pop(earliest)[1:])
            return ready

class FrameAssembler:
    def __init__(self):
        self.current_timestamp = None
        self.parts = []
        self.seq_start = None
        self.seq_end = None
    def add_packet(self, seq, payload, rtp_info):
        ts = rtp_info.get('timestamp')
        if self.current_timestamp is None:
            self.current_timestamp = ts
            self.parts = [payload]
            self.seq_start = seq
            self.seq_end = seq
            return None
        if ts == self.current_timestamp:
            self.parts.append(payload)
            self.seq_end = seq
            return None
        else:
            # flush previous frame
            frame_payload = b''.join(self.parts)
            frame = Frame(timestamp=self.current_timestamp, payload=frame_payload,
                          seq_range=(self.seq_start, self.seq_end), pts=self.current_timestamp)
            # start new frame
            self.current_timestamp = ts
            self.parts = [payload]
            self.seq_start = seq
            self.seq_end = seq
            return frame

class ClientCache:
    def __init__(self, max_frames=300):
        self.jb = JitterBuffer(max_hold_ms=150)
        self.assembler = FrameAssembler()
        self.cache = LRUCache(max_items=max_frames)
        self.frame_queue = deque()
        self.lock = threading.Lock()
    def on_rtp_packet(self, seq, payload, rtp_info):
        self.jb.push_packet(seq, payload, rtp_info)
        ready = self.jb.pop_ready_packets()
        for entry in ready:
            s = entry[0]
            payload2, rtp_info2 = entry[1], entry[2]
            frame = self.assembler.add_packet(s, payload2, rtp_info2)
            if frame:
                key = (frame.timestamp, frame.seq_range[0])
                self.cache.put(key, frame)
                with self.lock:
                    self.frame_queue.append(key)
        # if marker present, try to force flush by creating a new timestamp in assembler
        if rtp_info.get('marker', 0) == 1:
            maybe = self.assembler.add_packet(seq+1, b'', {'timestamp': (self.assembler.current_timestamp or 0) + 1})
            if maybe:
                key = (maybe.timestamp, maybe.seq_range[0])
                self.cache.put(key, maybe)
                with self.lock:
                    self.frame_queue.append(key)
    def get_frame(self, block=True, timeout_s=0.05):
        end = time.time() + timeout_s
        while True:
            with self.lock:
                if self.frame_queue:
                    key = self.frame_queue.popleft()
                    frame = self.cache.get(key)
                    return frame
            if not block:
                return None
            if time.time() > end:
                return None
            time.sleep(0.005)
    def cache_stats(self):
        return {'items': len(self.cache)}
