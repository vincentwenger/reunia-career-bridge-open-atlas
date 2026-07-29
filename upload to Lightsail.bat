:: Use your locally configured Docker and AWS credentials. Never store passwords in this file.
:: Locally, create docker image:
cd C:\Users\vince\Software\Reunia_Career_Bridge\v3\server\app
docker build -t reunia-career-bridge .

:: Then use it in Amazon lightsail as a container:
:: Push, view, and delete container images for a Lightsail container service
:: docker images
:: aws lightsail push-container-image --region <Region> --service-name <ContainerServiceName> --label <ContainerImageLabel> --image <LocalContainerImageName>:<ImageTag>
aws lightsail push-container-image --region us-west-2 --service-name reunia-career-bridge --label test --image reunia-career-bridge:latest

:: Public endpoint: port 8000, protocol HTTP; health check /health