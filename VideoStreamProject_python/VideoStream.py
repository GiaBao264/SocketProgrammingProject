class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		
		# Detect file format
		self.file.seek(0)
		first_bytes = self.file.read(5)
		self.file.seek(0)
        
        # Check if it's standard MJPEG (starts with JPEG marker)
		if first_bytes[:2] == b'\xff\xd8':
			self.is_standard_mjpeg = True
			print("Detected standard MJPEG format (JPEG markers)")
		else:
			self.is_standard_mjpeg = False
			print("Detected proprietary MJPEG format (length header)")

	def nextFrame(self):
		"""Get next frame - supports both proprietary and standard MJPEG."""
		if self.is_standard_mjpeg:
			return self._nextFrameStandard()
		else:
			return self._nextFrameProprietary()

	def _nextFrameProprietary(self):
		"""Original format: 5-byte length header + frame data."""
		data = self.file.read(5) # Get the framelength from the first 5 bits
		if data: 
			framelength = int(data)
							
			# Read the current frame
			data = self.file.read(framelength)
			self.frameNum += 1
		return data
		
	def _nextFrameStandard(self):
		"""Standard MJPEG: Find frames by JPEG markers (0xFFD8...0xFFD9)."""
        # Find start marker
		frame_data = bytearray()
        
        # Look for JPEG start marker (0xFFD8)
		byte = self.file.read(1)
		if not byte:
			return None
            
		while byte:
			if byte == b'\xff':
				next_byte = self.file.read(1)
				if next_byte == b'\xd8':
                    # Found start marker
					frame_data = bytearray(b'\xff\xd8')
					break
			byte = self.file.read(1)
        
		if not frame_data:
			return None
        
        # Read until end marker (0xFFD9)
		while True:
			byte = self.file.read(1)
			if not byte:
				break
            
			frame_data.append(byte[0])
            
            # Check for end marker
			if len(frame_data) >= 2:
				if frame_data[-2] == 0xff and frame_data[-1] == 0xd9:
                    # Found complete frame
					self.frameNum += 1
					return bytes(frame_data)
        
		return None

	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum
	
	