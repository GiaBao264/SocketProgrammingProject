import socket

HOST = '127.0.0.1' #local host
PORT = 1234

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#s.connect(("127.0.0.1", 1234))
s.connect((HOST, PORT))

try:
	while True:
		msg = input("Client: ")

	#msg = "I am client"

		s.sendall(bytes(msg, "utf8"))

		if msg == "quit":
			break
				
		data = s.recv(1024)
		str_data = data.decode("utf8")
		print("Server:", str_data)
		
		if str_data == "quit":
			break
		if not data:
			break
except:
	print("Disconnected")
finally:
	s.close()