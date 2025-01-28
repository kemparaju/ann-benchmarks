import docker

docker_client = docker.from_env()
docker_tags = {tag.split(":")[0] for image in docker_client.images.list() for tag in image.tags}
