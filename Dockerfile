FROM python:3.12-slim

# Ngăn Python tạo các file .pyc và bật log unbuffered
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cài đặt các thư viện hệ thống cần thiết cho Odoo
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    fontconfig \
    libpq-dev \
    libxslt1-dev \
    libxml2-dev \
    libldap2-dev \
    libsasl2-dev \
    libjpeg-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    zlib1g-dev \
    xfonts-75dpi \
    xfonts-base \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g rtlcss \
    && curl -sL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb -o wkhtmltox.deb \
    && apt-get install -y ./wkhtmltox.deb \
    && rm wkhtmltox.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc
WORKDIR /app

# Cài đặt Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn vào Container
COPY . .

# Expose cổng mặc định của Odoo
EXPOSE 8069

# Khởi chạy Odoo, cấu hình host trỏ sang container Database và chỉ định tên database
CMD ["python3", "./odoo-bin", "-c", "odoo.conf", "--db_host=db", "-d", "bhsoft_v20_db"]
