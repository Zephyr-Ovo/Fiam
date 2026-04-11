#!/bin/bash
/usr/bin/cp /root/Fiam/fiam.toml.example /root/Fiam/fiam.toml
sed -i 's/embedding_backend = "local"/embedding_backend = "remote"/' /root/Fiam/fiam.toml
sed -i 's|embedding_remote_url = ""|embedding_remote_url = "http://127.0.0.1:8819"|' /root/Fiam/fiam.toml
grep "embedding_backend\|embedding_remote_url" /root/Fiam/fiam.toml
echo "fiam.toml OK"
