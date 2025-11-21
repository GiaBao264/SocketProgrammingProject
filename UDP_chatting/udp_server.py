import socket

# Configuration
HOST = '127.0.0.1' 
PORT = 9999
BUFFER_SIZE = 1024

# Create a UDP socket
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.bind((HOST, PORT))
print(f"UDP Server running on {HOST}:{PORT}. Waiting for client...")

# Global variable to store the client's address for replying
client_addr = None

try:
    while True:
        # === RECEIVE FROM CLIENT ===
        print("\nWaiting for client message...")
        data, addr = server_socket.recvfrom(BUFFER_SIZE)
        
        # Store the client address if it's the first message
        if client_addr is None:
            client_addr = addr
            print(f"Client connected from {addr[1]}")
            
        message = data.decode('utf-8')
        
        if message.lower() == 'quit':
            print("Client sent 'quit'. Shutting down.")
            break
            
        print(f"[Client {addr[1]}] received: {message}")
        
        # === SEND TO CLIENT ===
        reply = input("Server > ")
        
        if reply.lower() == 'quit':
            # Optionally send 'quit' notification before breaking
            server_socket.sendto("quit".encode('utf-8'), client_addr)
            print("Server shutting down.")
            break
            
        server_socket.sendto(reply.encode('utf-8'), client_addr)

except KeyboardInterrupt:
    print("\nServer manually interrupted.")
except Exception as e:
    print(f"An error occurred: {e}")
finally:
    server_socket.close()
    print("Server socket closed.")