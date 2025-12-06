from random import randint
import sys, traceback, threading, socket, time

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	clientInfo = {}
	
	rtpSeqNum = 0

	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		self.rtpSeqNum = 0
		self.frameTimestamp = 0
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
		"""Receive RTSP request from the client."""
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		
		# Get the media file name
		filename = line1[1]
		
		# Get the RTSP sequence number 
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)
				
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq[1])
				
				# Get the RTP/UDP port from the last line
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING

				self.rtpSeqNum = 0
				self.frameTimestamp = 0

				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				self.clientInfo["rtpSocket"].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4194304)  # 4MB buffer
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				self.clientInfo['event'].set()
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			self.clientInfo['event'].set()
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()
			
	def sendRtp(self):
		"""Send RTP packets over UDP with HD fragmentation support."""
		while True:
			self.clientInfo['event'].wait(0.04)   
			
			# Stop sending if request is PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet(): 
				print("=== Server stopped sending (PAUSE/TEARDOWN) ===")
				break 
				
			data = self.clientInfo['videoStream'].nextFrame()
			if data: 
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])
					
					# Fragment large frame into packets =====
					fragments = RtpPacket.fragmentPayload(data)

					# Get timestamp for this frame (all fragments share same timestamp)
					currentFrameTimestamp = self.frameTimestamp  # Lưu timestamp
										
					if len(fragments) > 1:
						print(f"Frame {frameNumber}: {len(data)} bytes -> {len(fragments)} fragments, timestamp: {currentFrameTimestamp}")
					
					# Send all fragments with same timestamp
					for i, fragment in enumerate(fragments):
						# Mark the last fragment by marker bit = 1
						isLastFragment = (i == len(fragments) - 1)
						
						# Create RTP packet with specific sequence number for each fragment
						packet = self.makeRtp(fragment, self.rtpSeqNum, isLastFragment, currentFrameTimestamp)	
						
						# Send packet
						self.clientInfo['rtpSocket'].sendto(packet, (address, port))
						
						# Increase sequence number
						self.rtpSeqNum = (self.rtpSeqNum + 1) % 65536  # Handle wraparound

					self.frameTimestamp += 3600
						
					# Small delay between fragments to avoid network overload
					time.sleep(0.001)  
					
				except Exception as e:
					print(f"Connection Error: {e}")
					break

	def makeRtp(self, payload, seqNum, isLastFragment=True, timestamp=None):
		"""RTP-packetize the video data with fragmentation support."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		marker = 1 if isLastFragment else 0  # Marker bit = 1 for the last fragment 
		pt = 26 # MJPEG type
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		
		rtpPacket.encode(version, padding, extension, cc, seqNum, marker, pt, ssrc, payload, timestamp)
		
		return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")