from urllib.request import urlopen, urlretrieve

source_url="https://ann-benchmarks.com/glove-100-angular.hdf5"
destination_path="data/glove-100-angular.hdf5"
urlretrieve(source_url, destination_path)


