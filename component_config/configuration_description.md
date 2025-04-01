
The sample `/data` folder can be found in the [component's repository](https://github.com/gopaydata/gopay.ex-opensearch-v2/blob/main/component_config/sample-config/). The [config.json](https://github.com/gopaydata/gopay.ex-opensearch-v2/blob/main/component_config/sample-config/out/config.json) file represents the configuration, that should be passed to the component in order for the component to run successfully.

In Keboola, the component is set up as a row-based component and thus certain parameters (SSH & DB settings) have to be configured only once, while index specific settings can be configured for each index separately.

## Database and SSH Settings

OpenSearch extractor currently supports only connection to the OpenSearch instance over SSH tunnel. For successful connection, all database and SSH properties must be configured.

### Database (`db`) settings

The database host and port need to be provided to correctly connect to the engine and download index data.

Required parameters are:

- **Hostname** (`db.hostname`) - specifies the IP address or URL at which the database is located;
- **Port** (`db.port`) - specifies the accompanying port to the hostname.

The correct JSON specification of the database settings then takes the following form.

```json
{
  ...
  "db": {
      "hostname": "127.0.0.1",
      "port": 8080
    }
  ...
}
```

### SSH (`ssh`) settings

Connection to the OpenSearch instance via an SSH server is supported by the extractor.

Required parameters for SSH section of the configuration are:

- **SSH Hostname** (`ssh.hostname`) - a SSH host, to which a connection shall be made;
- **SSH Port** (`ssh.port`) - an accompanying SSH port to `ssh.hostname`;
- **SSH Username** (`ssh.username`) - a user, which will be used for SSH authentication;
- **SSH Private Key** (`ssh.#private_key`) - an SSH private key.

The final SSH configuration should then look like the one below.

```json
{
  ...
  "ssh": {
      "hostname": "ssh-host-url.cz",
      "port": 22,
      "username": "user-ssh",
      "#private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nENCRYPTED\nSSH\nKEY\n-----END OPENSSH PRIVATE KEY-----"
    }
  ...
}
```

*Note:* If you're using a predefined JSON configuration schema, the new lines in SSH private key will be automatically replaced by `\n`. However, if you're using the raw JSON to configure the component, you need to escape all new lines by `\n`, in order to inject the private key into the configuration properly.

## Row (index) configuration

Index configuration is tied to a specific index you'd like to download. The extraction can be fully customized using the following parameters:

### Index Name (`index_name`)

Specifies the name of the index to be downloaded from the OpenSearch instance.

### Query (`request_body`) *(optional)*

Optional custom OpenSearch query in JSON format. You can omit the `size` attribute here — the component will automatically handle it to optimize memory usage (defaults to 1000 documents per batch).

### Query Size (`size`) *(optional)*

Specifies the maximum number of documents per batch if not specified directly in the query body. Default is `1000`.

### Scroll Batch Size (`scroll_size`) *(optional)*

Specifies how many documents to retrieve per scroll batch. Default is `1000`. This is useful for controlling memory usage when working with large datasets.

### Time Window (Minutes) (`time_window_minutes`) *(optional)*

Specifies the default time window in minutes for a generated range query, if a custom query (`request_body`) is not provided. Default is `5` minutes.

### Output Table Name (`storage_table`)

Name of the output table under which the extracted data will be stored in Keboola Storage.

### Primary Keys (`primary_keys`)

An array of columns defining the primary key(s) of the output table.

### Load Type (`incremental`)

Specifies whether to use:
- **Incremental Load** (`true`): Appends only new or changed data.
- **Full Load** (`false`): Reloads the entire dataset.
