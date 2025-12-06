from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import time


from RtpPacket import RtpPacket
from client_cache import ClientCache
import time

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

	# Initiation..
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
		self.master.destroy() # Close the gui window
		try:
			os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video
		except:
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
				self.rtpThread = threading.Thread(target=self.listenRtp)
				self.rtpThread.daemon = True
				self.rtpThread.start()
			
			self.sendRtspRequest(self.PLAY)
	
	def listenRtp(self):
		"""Listen for RTP packets with HD fragmentation support."""
		frameFragments = []       
		currentTimestamp = None

		# Reset expected sequence number tracking for new play session
		# Don't reset global expectedSeqNum, just track locally
		lastSeqNum = None
		consecutiveTimeouts = 0

		print(f"\n=== Starting RTP listener ===\n")

		while True:

			if self.playEvent and self.playEvent.isSet():
				print("=== RTP listener stopped (PAUSE/TEARDOWN) ===")
				frameFragments = []
				currentTimestamp = None
				break

			try:
				with self.fragmentLock:
					if self.shouldClearBuffer:
						print(">>> Clearing fragment buffer (PAUSE/PLAY detected)")
						frameFragments = []
						currentTimestamp = None
						lastSeqNum = None
						self.shouldClearBuffer = False

				data = self.rtpSocket.recv(65536) # Increased buffer for HD
				if data:
					# Reset timeout counter
					consecutiveTimeouts = 0
					
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					currSeqNum = rtpPacket.seqNum()
					timestamp = rtpPacket.timestamp()
					marker = rtpPacket.marker()

					# if lastSeqNum is None:
					# 	lastSeqNum = currSeqNum
					# 	self.totalExpectedPackets += 1
					# 	continue

					#self.frameNbr += 1

					self.totalPackets += 1
					self.totalBytes += len(data)
					
					# Check for packet loss (only if not first packet in session)
					if lastSeqNum is not None:
						expectedNext = (lastSeqNum + 1) % 65536  # Handle wraparound

						if currSeqNum != expectedNext:
							# Calculate lost packets considering wraparound
							if currSeqNum > expectedNext:
								lostCount = currSeqNum - expectedNext
							else:
								# Wraparound case: from 65535 to 0
								lostCount = (65536 - expectedNext) + currSeqNum
							
							# Only count as loss if reasonable (not a PAUSE/PLAY gap)
							if lostCount < 100:  # Reasonable threshold
								self.lostPackets += lostCount
								self.totalExpectedPackets += lostCount
								print(f"Packet loss! Expected: {currSeqNum}, Got: {expectedNext}, Lost: {lostCount}")
					
					self.totalExpectedPackets += 1
					lastSeqNum = currSeqNum
					
					# Handle fragmentation with timestamp grouping
					if currentTimestamp is None:
						# First packet after start/resume
						print(f">>> Starting new frame with timestamp: {timestamp}")
						frameFragments = [rtpPacket.getPayload()]
						currentTimestamp = timestamp
					elif timestamp != currentTimestamp:
						# New frame detected by timestamp change
						if frameFragments:
							print(f">>> Incomplete frame discarded (had {len(frameFragments)} fragments, no marker bit)")
						
						print(f">>> New frame started, timestamp: {currentTimestamp} -> {timestamp}")
						frameFragments = [rtpPacket.getPayload()]
						currentTimestamp = timestamp
					else:
						# Same frame, accumulate fragments
						frameFragments.append(rtpPacket.getPayload())
					
					# Marker bit indicates last fragment
					if marker == 1:
						# Reassemble complete frame
						completeFrame = b''.join(frameFragments)
						self.frameNbr += 1
						
						print(f"Frame {self.frameNbr}: {len(frameFragments)} fragment(s), {len(completeFrame)} bytes, timestamp: {timestamp}")
						
						# Update display
						try:
							imagePath = self.writeFrame(completeFrame)
							if imagePath:
								self.updateMovie(imagePath)
						except Exception as e:
							print(f"Error displaying frame {self.frameNbr}: {e}")
						
						# Update statistics
						self.updateStats()
						self.checkNetworkQuality()
						
						# Reset for next frame
						frameFragments = []
						currentTimestamp = None
						self.lastFrameTime = time.time()

			except socket.timeout:
				consecutiveTimeouts += 1
				# Check if we're not receiving frames
				if self.state == self.PLAYING and not self.playEvent.isSet():
					timeSinceLastFrame = time.time() - self.lastFrameTime
					if timeSinceLastFrame > 3.0 and self.frameNbr > 0:
							if consecutiveTimeouts > 6 and not self.videoEnded:  # 6 timeouts = 3 seconds
								print("\n=== Video stream ended (no more frames from server) ===")
								self.videoEnded = True
								self.updateNetworkStatus("Stream Ended", "blue")
								# Don't show warning if video naturally ended
								consecutiveTimeouts = 0  # Reset to avoid spam
				continue
				
			except Exception as e:
				print(f"RTP Error: {e}")
				# Stop listening upon requesting PAUSE or TEARDOWN
				if self.playEvent and self.playEvent.isSet(): 
					print("=== RTP listener stopped (PAUSE/TEARDOWN) ===")
					frameFragments = []
					currentTimestamp = None
					break
				
				# Upon receiving ACK for TEARDOWN request
				if self.teardownAcked == 1:
					try:
						self.rtpSocket.shutdown(socket.SHUT_RDWR)
						self.rtpSocket.close()
					except:
						pass
					break

	def checkNetworkQuality(self):
		"""Check network quality and show warnings."""
		if self.totalPackets < 50:
			return
		
		# Don't check if video ended
		if self.videoEnded:
			return

		lossRate = (self.lostPackets / self.totalExpectedPackets * 100) if self.totalExpectedPackets > 0 else 0
		
		# Calculate bitrate
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
		self.networkLabel.config(text=f"Network: {status}", fg=color)
	
	def showNetworkWarning(self, level, message):
		"""Show network quality warning."""
		if level == "CRITICAL":
			tkMessageBox.showerror("Critical Network Issue", 
				f"Connection unstable!\n\n{message}\n\nStreaming interrupted.")
		elif level == "POOR":
			tkMessageBox.showwarning("Poor Network Quality", 
				f"Network connection is poor!\n\n{message}\n\nConsider checking your connection.")
		elif level == "UNSTABLE":
			print(f"Network Warning: {message}")
					
	def updateStats(self):
		"""Update statistics display."""
		lossRate = (self.lostPackets / self.totalExpectedPackets * 100) if self.totalExpectedPackets > 0 else 0
		
		elapsed = time.time() - self.startTime if self.startTime else 1
		bitrate = (self.totalBytes * 8) / (elapsed * 1000000)
		
		statsText = (f"Frames: {self.frameNbr} | Packets: {self.totalPackets} | "
					f"Lost: {self.lostPackets} ({lossRate:.2f}%) | "
					f"Bitrate: {bitrate:.2f} Mbps | Data: {self.totalBytes/1024/1024:.2f} MB")
		self.statsLabel.config(text=statsText)

	def writeFrame(self, data):
		"""Write the received frame to a temp image file."""
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		try:
			with open(cachename, "wb") as file:
				file.write(data)
			return cachename
		except Exception as e:
			print(f"Error writing frame: {e}")
			return None
	
	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		if imageFile is None:
			return
		try:
			photo = ImageTk.PhotoImage(Image.open(imageFile))
			w, h = Image.open(imageFile).size
			self.label.configure(image=photo, height=h, width=w)
			self.label.image = photo
		except Exception as e:
			print(f"Error updating frame: {e}")
		
	def connectToServer(self):
		"""Connect to the Server."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
			print(f"Connected to server: {self.serverAddr}:{self.serverPort}")
		except Exception as e:
			tkMessageBox.showwarning('Connection Failed', f'Connection to {self.serverAddr} failed.\n{e}')
	
	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""	
		# Setup request
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply, daemon=True).start()
			self.rtspSeq += 1
			request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
			self.requestSent = self.SETUP
		
		# Play request
		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			self.requestSent = self.PLAY
		
		# Pause request
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			self.requestSent = self.PAUSE
			
		# Teardown request
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			self.rtspSeq += 1
			request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			self.requestSent = self.TEARDOWN
		else:
			return
		
		self.rtspSocket.send(request.encode())
		print(f'\nData sent:\n{request}\n')

	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			try:
				reply = self.rtspSocket.recv(1024)
				
				if reply: 
					self.parseRtspReply(reply.decode("utf-8"))
				
				# Close the RTSP socket upon requesting Teardown
				if self.requestSent == self.TEARDOWN:
					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					break
			except Exception as e:
				print(f"RTSP receive error: {e}")
				break
	
	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
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

	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.rtpSocket.settimeout(0.5)
		
		# Increase receive buffer for HD streaming
		self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4194304)  # 4MB
		
		try:
			self.rtpSocket.bind(('', self.rtpPort))
			print(f"Binded RTP port: {self.rtpPort}")
		except Exception as e:
			tkMessageBox.showwarning('Unable to Bind', f'Unable to bind PORT={self.rtpPort}\n{e}')


	def _display_loop(self):
		"""Background thread: pull assembled frames from cache and display them"""
		while True:
			if self.state != Client.PLAYING:
				time.sleep(0.02)
				continue
			frame = self.cache.get_frame(block=True, timeout_s=0.1)
			if frame is None:
				continue
			# write frame payload to cache file and update GUI
			try:
				imageFile = self.writeFrame(frame.payload)
				self.updateMovie(imageFile)
			except Exception as e:
				print("Error displaying frame from cache:", e)

	\tdef handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else:
			self.playMovie()