# Use the official Red Hat Universal Base Image 8 with Python 3.9
FROM registry.access.redhat.com/ubi8/python-39

# Switch to root user to install system dependencies if needed
USER 0

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first to leverage Docker's layer caching
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application's source code
COPY . .

# Expose the port the app runs on
EXPOSE 5001

# Switch to a non-root user for better security
USER 1001

# Define the command to run the application using Gunicorn
# This is a production-ready WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "app:app"]