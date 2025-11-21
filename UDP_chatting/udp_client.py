import socket

# Configuration
SERVER_HOST = '127.0.0.1' 
SERVER_PORT = 9999
BUFFER_SIZE = 1024
SERVER_ADDR = (SERVER_HOST, SERVER_PORT)

# Create a UDP socket
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
print(f"UDP Client ready. Connecting to {SERVER_HOST}:{SERVER_PORT}.")

try:
    while True:
        # === SEND TO SERVER (MUST SEND FIRST) ===
        message = input("Client > ")
        
        if message.lower() == 'quit':
            client_socket.sendto("quit".encode('utf-8'), SERVER_ADDR)
            print("Client shutting down.")
            break
            
        client_socket.sendto(message.encode('utf-8'), SERVER_ADDR)
        
        # === RECEIVE FROM SERVER ===
        print("Waiting for server reply...")
        data, addr = client_socket.recvfrom(BUFFER_SIZE)
        reply = data.decode('utf-8')
        
        if reply.lower() == 'quit':
            print("[Server] received: Server shut down.")
            break
            
        print(f"[Server] received: {reply}")
        
except KeyboardInterrupt:
    print("\nClient manually interrupted.")
except Exception as e:
    print(f"An error occurred: {e}")
finally:
    client_socket.close()
    print("Client socket closed.")