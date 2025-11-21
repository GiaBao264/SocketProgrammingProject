import socket
#HOST = the host IP
#PORT = port of the server application
HOST = '127.0.0.1' 
PORT = 1234
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

s.bind((HOST, PORT))
s.listen()
conn, addr = s.accept()
try:
	while True:
		data = conn.recv(1024)
		str_data = data.decode("utf8")
		print("Client:",str_data)

		if not data:
			break
		if str_data == "quit":
			break
		
		msg = input("Server: ")
		conn.sendall(bytes(msg, "utf8"))
		if msg == "quit":
			break
except:
	print("Disconnected")
finally:
	conn.close()
	s.close()
