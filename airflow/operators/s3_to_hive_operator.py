import logging
from tempfile import NamedTemporaryFile

from airflow.hooks import HiveCliHook, S3Hook
from airflow.models import BaseOperator
from airflow.utils import apply_defaults


class S3ToHiveTransfer(BaseOperator):
    """
    Moves data from S3 to Hive. The operator downloads a file from S3,
    stores the file locally before loading it into a Hive table.
    If the ``create`` or ``recreate`` arguments are set to ``True``,
    a ``CREATE TABLE`` and ``DROP TABLE`` statements are generated.
    Hive data types are inferred from the cursors's metadata from.

    Note that the table generated in Hive uses ``STORED AS textfile``
    which isn't the most efficient serialization format. If a
    large amount of data is loaded and/or if the tables gets
    queried considerably, you may want to use this operator only to
    stage the data into a temporary table before loading it into its
    final destination using a ``HiveOperator``.

    :param s3_key: The key to be retrieved from S3
    :type s3_key: str
    :param field_dict: A dictionary of the fields name in the file
        as keys and their Hive types as values
    :type field_dict: dict
    :param hive_table: target Hive table, use dot notation to target a
        specific database
    :type hive_table: str
    :param create: whether to create the table if it doesn't exist
    :type create: bool
    :param recreate: whether to drop and recreate the table at every
        execution
    :type recreate: bool
    :param partition: target partition as a dict of partition columns
        and values
    :type partition: dict
    :param headers: whether the file contains column names on the first
        line
    :type headers: bool
    :param headers: whether the column names on the first line should be
        checked against the keys of field_dict
    :type headers: bool
    :param delimiter: field delimiter in the file
    :type delimiter: str
    :param s3_conn_id: source s3 connection
    :type s3_conn_id: str
    :param hive_conn_id: desctination hive connection
    :type hive_conn_id: str
    """

    __mapper_args__ = {
        'polymorphic_identity': 'S3ToHiveOperator'
    }
    template_fields = ('s3_key', 'partition', 'hive_table')
    template_ext = ()
    ui_color = '#a0e08c'

    @apply_defaults
    def __init__(
            self,
            s3_key,
            field_dict,
            hive_table,
            delimiter=',',
            create=True,
            recreate=False,
            partition=None,
            headers=False,
            check_headers=False,
            s3_conn_id='s3_default',
            hive_cli_conn_id='hive_cli_default',
            *args, **kwargs):
        super(S3ToHiveTransfer, self).__init__(*args, **kwargs)
        self.s3_key = s3_key
        self.field_dict = field_dict
        self.hive_table = hive_table
        self.delimiter = delimiter
        self.create = create
        self.recreate = recreate
        self.partition = partition
        self.headers = headers
        self.check_headers = check_headers
        self.hive = HiveCliHook(hive_cli_conn_id=hive_cli_conn_id)
        self.s3 = S3Hook(s3_conn_id=s3_conn_id)

    def execute(self, context):
        logging.info("Downloading S3 file")
        if not self.s3.check_for_key(self.s3_key):
            raise Exception("The key {0} does not exists".format(self.s3_key))
        s3_key_object = self.s3.get_key(self.s3_key)
        with NamedTemporaryFile("w") as f:
            logging.info("Dumping S3 file {0} contents to local"
                         " file {1}".format(self.s3_key, f.name))
            s3_key_object.get_contents_to_file(f)
            f.flush()
            self.s3.connection.close()
            if not self.headers:
                logging.info("Loading file into Hive")
                self.hive.load_file(
                    f.name,
                    self.hive_table,
                    field_dict=self.field_dict,
                    create=self.create,
                    partition=self.partition,
                    delimiter=self.delimiter,
                    recreate=self.recreate)
            else:
                with open(f.name, 'r') as tmpf:
                    if self.check_headers:
                        header_l = tmpf.readline()
                        header_line = header_l.rstrip()
                        header_list = header_line.split(self.delimiter)
                        field_names = list(self.field_dict.keys())
                        test_field_match = [h1.lower() == h2.lower() for h1, h2
                                            in zip(header_list, field_names)]
                        if not all(test_field_match):
                            logging.warning("Headers do not match field names"
                                            "File headers:\n {header_list}\n"
                                            "Field names: \n {field_names}\n"
                                            "".format(**locals()))
                            raise Exception("Headers do not match the "
                                            "field_dict keys")
                    with NamedTemporaryFile("w") as f_no_headers:
                        tmpf.seek(0)
                        next(tmpf)
                        for line in tmpf:
                            f_no_headers.write(line)
                        f_no_headers.flush()
                        logging.info("Loading file without headers into Hive")
                        self.hive.load_file(
                            f_no_headers.name,
                            self.hive_table,
                            field_dict=self.field_dict,
                            create=self.create,
                            partition=self.partition,
                            delimiter=self.delimiter,
                            recreate=self.recreate)
