
FROM nginx:alpine

COPY index.html /usr/share/nginx/html/index.html
COPY register.html /usr/share/nginx/html/register.html
COPY dashboard.html /usr/share/nginx/html/dashboard.html
COPY kitchen.html /usr/share/nginx/html/kitchen.html

EXPOSE 80
