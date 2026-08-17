#!/bin/bash
set -e

mkdir -p certs
cd certs

echo "Generating local CA..."
openssl req -new -x509 -days 3650 -extensions v3_ca \
    -keyout ca.key -out ca.crt \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=LocalCA" \
    -nodes

echo "Generating server key..."
openssl genrsa -out server.key 2048

echo "Generating server CSR..."
openssl req -new -key server.key -out server.csr \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost"

echo "Signing server cert..."
cat <<EOF > v3.ext
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names
[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days 3650 -extfile v3.ext

echo "Fixing permissions for Mosquitto..."
chmod 644 ca.crt server.crt server.key

echo "Certificates generated successfully in ./certs"
cd ..
