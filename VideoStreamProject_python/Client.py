from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, os, time, io

from RtpPacket import RtpPacket
from client_cache import ClientCache

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # Network quality thresholds
    LOSS_RATE_WARNING = 5.0
    LOSS_RATE_CRITICAL = 10.0

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0

        # Statistics
        self.totalBytes = 0
        self.totalPackets = 0
        self.totalExpectedPackets = 0
        self.lostPackets = 0

        self.startTime = None
        self.lastWarningTime = 0

        # Network quality monitoring
        self.windowSize = 100
        self.lastFrameTime = time.time()

        # RTP listener control
        self.rtpThread = None
        self.playEvent = None
        self.videoEnded = False

        # Shared state for fragment buffer
        self.fragmentLock = threading.Lock()
        self.shouldClearBuffer = False

        # Client-side cache and display thread
        # client_cache.py handles jitter buffer + frame assembly. See file: client_cache.py. :contentReference[oaicite:4]{index=4}
        self.cache = ClientCache(max_frames=600)

        # Start display thread (pulls completed frames from cache and schedules GUI update)
        self.displayThread = threading.Thread(target=self._display_loop, daemon=True)
        self.displayThread.start()

        # Minimal debug flag (turn True only for troubleshooting)
        self.debug = False

    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        # Create Play button
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] =  self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

        # Stats label
        self.statsLabel = Label(self.master, text="Stats: Waiting...", font=("Arial", 10))
        self.statsLabel.grid(row=2, column=0, columnspan=4, sticky=W, padx=5)

        # Network quality indicator
        self.networkLabel = Label(self.master, text="Network: Unknown",
                                 font=("Arial", 10, "bold"), fg="gray")
        self.networkLabel.grid(row=3, column=0, columnspan=4, sticky=W, padx=5)

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except Exception:
            pass
        try:
            self.master.destroy()
        except Exception:
            pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            with self.fragmentLock:
                self.shouldClearBuffer = True

            self.sendRtspRequest(self.PAUSE)
            # Stop the RTP listening thread
            if self.playEvent:
                self.playEvent.set()

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY:
            # Reset video ended flag
            self.videoEnded = False

            # Start timing if first play
            if self.startTime is None:
                self.frameNbr = 0
                self.totalBytes = 0
                self.totalPackets = 0
                self.totalExpectedPackets = 0
                self.lostPackets = 0
                self.startTime = time.time()

            self.lastFrameTime = time.time()

            with self.fragmentLock:
                self.shouldClearBuffer = True

            if self.rtpThread is None or not self.rtpThread.is_alive():
                # Create a thread to listen for RTP packets
                self.playEvent = threading.Event()
                self.playEvent.clear()

                # Start new RTP listener thread
                self.rtpThread = threading.Thread(target=self.listenRtp, daemon=True)
                self.rtpThread.start()

            self.sendRtspRequest(self.PLAY)

    def listenRtp(self):
        """Listen for RTP packets and hand payloads to ClientCache for assembly."""
        lastSeqNum = None
        consecutiveTimeouts = 0

        while True:
            if self.playEvent and self.playEvent.isSet():
                break

            try:
                with self.fragmentLock:
                    if self.shouldClearBuffer:
                        # recreate cache to clear internal buffers
                        self.cache = ClientCache(max_frames=600)
                        self.shouldClearBuffer = False
                        lastSeqNum = None

                data, _ = self.rtpSocket.recvfrom(65536)  # larger buffer for HD fragments
                if not data:
                    continue

                # decode RTP
                rtpPacket = RtpPacket()
                rtpPacket.decode(data)

                currSeqNum = rtpPacket.seqNum()
                timestamp = rtpPacket.timestamp()
                marker = rtpPacket.marker()
                payload = rtpPacket.getPayload()

                # update lightweight statistics
                self.totalPackets += 1
                self.totalBytes += len(data)

                # simple loss detection
                if lastSeqNum is not None:
                    expectedNext = (lastSeqNum + 1) % 65536
                    if currSeqNum != expectedNext:
                        gap = (currSeqNum - expectedNext) if currSeqNum >= expectedNext else (65536 - expectedNext + currSeqNum)
                        if gap < 100:
                            self.lostPackets += gap
                            self.totalExpectedPackets += gap
                self.totalExpectedPackets += 1
                lastSeqNum = currSeqNum

                # pass packet to cache (jitter buffer + assembler)
                rtp_info = {'timestamp': timestamp, 'marker': marker}
                try:
                    self.cache.on_rtp_packet(currSeqNum, payload, rtp_info)
                except Exception:
                    # keep running even if cache has an internal error
                    pass

            except socket.timeout:
                consecutiveTimeouts += 1
                # detect stream end
                if self.state == self.PLAYING and not (self.playEvent and self.playEvent.isSet()):
                    timeSinceLastFrame = time.time() - self.lastFrameTime
                    if timeSinceLastFrame > 3.0 and self.frameNbr > 0 and not self.videoEnded:
                        self.videoEnded = True
                        self.updateNetworkStatus("Stream Ended", "blue")
                continue
            except Exception:
                if self.playEvent and self.playEvent.isSet():
                    break
                if self.teardownAcked == 1:
                    try:
                        self.rtpSocket.close()
                    except Exception:
                        pass
                    break
                continue

    def checkNetworkQuality(self):
        """Check network quality and show warnings."""
        if self.totalPackets < 50 or self.videoEnded:
            return

        lossRate = (self.lostPackets / self.totalExpectedPackets * 100) if self.totalExpectedPackets > 0 else 0

        elapsed = time.time() - self.startTime if self.startTime else 1
        bitrate = (self.totalBytes * 8) / (elapsed * 1000000)

        currentTime = time.time()

        if lossRate > self.LOSS_RATE_CRITICAL or bitrate < 0.5:
            self.updateNetworkStatus("POOR", "red")
            if currentTime - self.lastWarningTime > 10:
                self.showNetworkWarning("POOR",
                    f"High packet loss: {lossRate:.1f}% | Bitrate: {bitrate:.2f} Mbps")
                self.lastWarningTime = currentTime

        elif lossRate > self.LOSS_RATE_WARNING:
            self.updateNetworkStatus("UNSTABLE", "orange")
            if currentTime - self.lastWarningTime > 15:
                self.showNetworkWarning("UNSTABLE",
                    f"Moderate packet loss: {lossRate:.1f}%")
                self.lastWarningTime = currentTime

        elif bitrate < 1.0:
            self.updateNetworkStatus("FAIR", "yellow")
        else:
            self.updateNetworkStatus("GOOD", "green")

    def updateNetworkStatus(self, status, color):
        """Update network status label."""
        try:
            self.networkLabel.config(text=f"Network: {status}", fg=color)
        except Exception:
            pass

    def showNetworkWarning(self, level, message):
        """Show network quality warning."""
        if level == "CRITICAL":
            tkMessageBox.showerror("Critical Network Issue",
                f"Connection unstable!\n\n{message}\n\nStreaming interrupted.")
        elif level == "POOR":
            tkMessageBox.showwarning("Poor Network Quality",
                f"Network connection is poor!\n\n{message}\n\nConsider checking your connection.")
        elif level == "UNSTABLE":
            # Non-blocking notification: keep in logs only
            pass

    def updateStats(self):
        """Update statistics display."""
        lossRate = (self.lostPackets / self.totalExpectedPackets * 100) if self.totalExpectedPackets > 0 else 0

        elapsed = time.time() - self.startTime if self.startTime else 1
        bitrate = (self.totalBytes * 8) / (elapsed * 1000000)

        statsText = (f"Frames: {self.frameNbr} | Packets: {self.totalPackets} | "
                    f"Lost: {self.lostPackets} ({lossRate:.2f}%) | "
                    f"Bitrate: {bitrate:.2f} Mbps | Data: {self.totalBytes/1024/1024:.2f} MB")
        try:
            self.statsLabel.config(text=statsText)
        except Exception:
            pass

    def _display_loop(self):
        """Background thread: pull assembled frames from cache and display them on main thread."""
        while True:
            if self.state != Client.PLAYING:
                time.sleep(0.02)
                continue
            frame = self.cache.get_frame(block=True, timeout_s=0.5)
            if frame is None:
                continue
            try:
                img = Image.open(io.BytesIO(frame.payload))
                # schedule UI update on main thread
                try:
                    self.master.after(0, self._display_image, img)
                except Exception:
                    self._display_image(img)
                # update stats
                self.frameNbr += 1
                self.updateStats()
                self.checkNetworkQuality()
                self.lastFrameTime = time.time()
            except Exception:
                continue

    def _display_image(self, img):
        """Make PhotoImage and set into label (must run on main thread)."""
        try:
            MAX_W, MAX_H = 1280, 720
            w, h = img.size
            if w > MAX_W or h > MAX_H:
                try:
                    resample_filter = Image.Resampling.LANCZOS
                except Exception:
                    if hasattr(Image, "LANCZOS"):
                        resample_filter = Image.LANCZOS
                    elif hasattr(Image, "ANTIALIAS"):
                        resample_filter = Image.ANTIALIAS
                    else:
                        resample_filter = 1
                img.thumbnail((MAX_W, MAX_H), resample_filter)
                w, h = img.size

            photo = ImageTk.PhotoImage(img)
            self.label.configure(image=photo, width=w, height=h)
            self.label.image = photo
            try:
                self.label.lift()
                self.label.update_idletasks()
            except Exception:
                pass
        except Exception:
            pass

    def connectToServer(self):
        """Connect to the Server."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except Exception as e:
            tkMessageBox.showwarning('Connection Failed', f'Connection to {self.serverAddr} failed.\n{e}')

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
            self.requestSent = self.SETUP

        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PLAY

        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PAUSE

        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.TEARDOWN
        else:
            return

        try:
            self.rtspSocket.send(request.encode())
        except Exception:
            pass

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply:
                    self.parseRtspReply(reply.decode("utf-8"))
                if self.requestSent == self.TEARDOWN:
                    try:
                        self.rtspSocket.shutdown(socket.SHUT_RDWR)
                        self.rtspSocket.close()
                    except Exception:
                        pass
                    break
            except Exception:
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        try:
            lines = data.split('\n')
            seqNum = int(lines[1].split(' ')[1])
            if seqNum == self.rtspSeq:
                session = int(lines[2].split(' ')[1])
                if self.sessionId == 0:
                    self.sessionId = session
                if self.sessionId == session:
                    if int(lines[0].split(' ')[1]) == 200:
                        if self.requestSent == self.SETUP:
                            self.state = self.READY
                            self.openRtpPort()
                        elif self.requestSent == self.PLAY:
                            self.state = self.PLAYING
                        elif self.requestSent == self.PAUSE:
                            self.state = self.READY
                            if self.playEvent:
                                self.playEvent.set()
                        elif self.requestSent == self.TEARDOWN:
                            self.state = self.INIT
                            self.teardownAcked = 1
                            if self.playEvent:
                                self.playEvent.set()
        except Exception:
            pass

    def openRtpPort(self):
        """Open RTP socket binded to a specified port."""
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        # Increase receive buffer for HD streaming
        self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4194304)  # 4MB
        try:
            self.rtpSocket.bind(('', self.rtpPort))
        except Exception as e:
            tkMessageBox.showwarning('Unable to Bind', f'Unable to bind PORT={self.rtpPort}\n{e}')

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
