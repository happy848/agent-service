# Use PostgreSQL 16 as the base image for better performance and features
FROM postgres:16-bullseye

# Add label information
LABEL maintainer="AI Brain Service Team"
LABEL description="PostgreSQL instance optimized for AI Brain Service workloads"

# Set environment variables for PostgreSQL
ENV POSTGRES_DB=ai_brain
ENV POSTGRES_USER=root
ENV POSTGRES_PASSWORD_FILE=postgres_password.txt

# Create directories for custom configurations and data
RUN mkdir -p /docker-entrypoint-initdb.d
RUN mkdir -p /var/lib/postgresql/data

# Copy custom PostgreSQL configuration
COPY postgresql.conf /etc/postgresql/postgresql.conf

# Set the custom configuration file as the default
CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]

# Expose PostgreSQL port
EXPOSE 5432

# Set healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD pg_isready -U $POSTGRES_USER -d $POSTGRES_DB || exit 1 