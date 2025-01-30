# eassy-pdf

<!-- isolate dependencies -->
python -m venv venv


source venv/Scripts/activate



<!-- to run on windows with mounted files go to app root  directory and using cmd run: -->

docker build -t eassy-pdf .

<!-- run from root directory of image  -->

docker run -d --name eassypdf -p 5000:80 -v "${PWD}:/usr/src/app" eassy-pdf
