import socket
import os

def get_local_ip():
    """
    Returns the local IPv4 address of the machine.
    Tries to prefer 192.168.x.x or 10.x.x.x (WiFi/LAN).
    """
    try:
        # Method 1: Check all interfaces and prefer common LAN ranges
        hostname = socket.gethostname()
        _, _, ip_list = socket.gethostbyname_ex(hostname)
        for ip in ip_list:
            if ip.startswith("192.168.") or ip.startswith("10."):
                return ip
        
        # Method 2: Create a dummy socket to find the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Use a non-routable IP to avoid actually sending data
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        
        # If the socket method returns a virtual adapter (WSL/Docker), 
        # and we have a LAN IP in our list, prefer the LAN IP.
        if (ip.startswith("172.1") or ip.startswith("172.2") or ip.startswith("172.3")) and ip_list:
            for alt_ip in ip_list:
                if alt_ip.startswith("192.168.") or alt_ip.startswith("10."):
                    return alt_ip
        
        return ip
    except Exception as e:
        return "127.0.0.1"

def get_configured_base_url(env_var, default_port=5173):
    """
    Gets the base URL from environment variables, 
    replacing localhost with the device IP if necessary.
    """
    base_url = (
        os.getenv(env_var)
        or os.getenv("PASSWORD_RESET_BASE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or os.getenv("RESET_LINK_BASE_URL")
        or f"http://localhost:{default_port}"
    ).rstrip("/")
    
    if "localhost" in base_url or "127.0.0.1" in base_url:
        local_ip = get_local_ip()
        base_url = base_url.replace("localhost", local_ip).replace("127.0.0.1", local_ip)
    
    return base_url
