from aiokafka.codec import has_lz4, has_snappy, has_zstd


def test_compression_codecs_installed():
    # Producers (rpk, the Java and Node services) may compress messages.
    # Without these codecs the worker crashes on the first compressed batch.
    assert has_snappy(), "install aiokafka[snappy]"
    assert has_lz4(), "install aiokafka[lz4]"
    assert has_zstd(), "install aiokafka[zstd]"
