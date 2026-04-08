import torch
print("-" * 30)
status = torch.cuda.is_available()
print(f"CUDA Status: {status}")
if status:
    print(f"GPU Device: {torch.cuda.get_device_name(0)}")
else:
    print("ALERT: Running on CPU! This is why it takes 7 minutes.")
print("-" * 30)