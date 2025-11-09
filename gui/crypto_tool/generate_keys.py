from Crypto.PublicKey import RSA

# 生成 2048 位的 RSA 密钥对
key = RSA.generate(2048)

# 导出私钥并保存
private_key = key.export_key()
with open("private.pem", "wb") as f:
    f.write(private_key)

# 导出一个公钥并保存
public_key = key.publickey().export_key()
with open("public.pem", "wb") as f:
    f.write(public_key)

print("密钥对已生成：private.pem 和 public.pem")