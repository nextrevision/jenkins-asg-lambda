# jenkins-asg-lambda

[![Circle CI](https://circleci.com/gh/nextrevision/jenkins-asg-lambda.svg?style=svg)](https://circleci.com/gh/nextrevision/jenkins-asg-lambda)

AWS [Lambda](https://aws.amazon.com/lambda/) function to call a [Jenkins](https://jenkins.io/index.html) job for an [ASG](http://docs.aws.amazon.com/AutoScaling/latest/DeveloperGuide/AutoScalingGroup.html) [lifecycle event](http://docs.aws.amazon.com/AutoScaling/latest/DeveloperGuide/AutoScalingGroupLifecycle.html).

This configurable Lambda function will call a Jenkins job when an ASG lifecycle event occurs. Currently, only the `EC2_INSTANCE_LAUNCHING` and `EC2_INSTANCE_TERMINATING` events are handled by this function.

## Installation

1. Create a Lambda function (theres a good write-up [here](https://aws.amazon.com/blogs/compute/using-aws-lambda-with-auto-scaling-lifecycle-hooks/)).
2. Determine your config strategy ([local](#local-config) or [S3](#s3-configs)) and create a configuration file (see [config.ini.sample](https://github.com/nextrevision/jenkins-asg-lambda/blob/master/config.ini.sample) for an example).
3. [Build](#building) or [download](https://github.com/nextrevision/jenkins-asg-lambda/releases) a zip file of the source and libraries.
4. Upload to Lambda.

## Configuration

The function supports a number of configurable parameters which are read from an INI file, either coupled with the function (`config.ini` in the root of the zip) or stored in S3. To see an example of a config file, please reference [config.ini.sample](https://github.com/nextrevision/jenkins-asg-lambda/blob/master/config.ini.sample).

### Sections

#### `DEFAULT`

- `use_credstash` (*boolean*): whether or not to use credstash for retrieving authentication credentials. If true, the `[credstash]` section needs to be filled out, otherwise, the `[jenkins]` section needs to include the credentials.
- `call_create_job` (*boolean*): call the configured Jenkins job when an `EC2_INSTANCE_LAUNCHING` event is received.
- `call_terminate_job` (*boolean*): call the configured Jenkins job when an `EC2_INSTANCE_TERMINATING` event is received.

#### `credstash`

[Credstash](https://github.com/fugue/credstash) is a utility to manage secrets that uses [AWS KMS](https://aws.amazon.com/kms/) and [AWS DynamoDB](https://aws.amazon.com/dynamodb/). When using credstash, the function must be able to read the KMS key and have access to the DynamoDB table otherwise the lookup will fail. The following is an example policy for accessing DynamoDB (KMS access can be assigned through the AWS IAM console):

```
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:*"
  ],
  "Resource": [
    "arn:aws:dynamodb:*:*:table/credential-store"
  ]
}
```

NOTE: when the term `key` is referred to, it is in reference to the key used when calling credstash. For example, if you stored the username to authenticate to Jenkins as `my_ci_server_username` (i.e. `credstash put my_ci_server_username user1`), that is the key. In this example, you would populate the setting `jenkins_username_key` as `my_ci_server_username`.

- `table` (*string*): the dynamodb table used by credstash to query for credentials
- `jenkins_username_key` (*string*): the name of the key that contains the username used to authenticate to Jenkins
- `jenkins_user_token_key` (*string*): the name of the key that contians the API key used to authenticate the user to Jenkins
- `jenkins_create_job_token_key` (*string*): the name of the key that contains the token used to remotely call the job's build API (see [https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security](https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security))
- `jenkins_terminate_job_token_key` (*string*): the name of the key that contains the token used to remotely call the job's build API (see [https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security](https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security))

#### `jenkins`

- `url` (*string*): the url of the Jenkins server
- `verify_ssl` (*boolean*): whether or not to validate the SSL cert assigned to the Jenkins server
- `username` (*optional*, *string*): if `use_credstash` is set to `false`, the username used to authenticate to Jenkins
- `api_key` (*optional*, *string*): if `use_credstash` is set to `false`, the api key used to authenticate the user to Jenkins
- `create_job` (*string*): the job to call when an `EC2_INSTANCE_LAUNCHING` event is received
- `create_job_token` (*optional*, *string*): if `use_credstash` is set to `false`, the token used to call the create job's build API (see [https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security](https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security))
- `create_job_params` (*string*): params to pass to the create job (if using a parameterized build (see [Job Parameters](#job-parameters) section)
- `terminate_job` (*string*): the job to call when an `EC2_INSTANCE_TERMINATING` event is received
- `terminate_job_token` (*optional*, *string*): if `use_credstash` is set to `false`, the token used to call the terminate job's build API (see [https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security](https://wiki.jenkins-ci.org/display/JENKINS/Quick+and+Simple+Security))
- `terminate_job_params` (*string*): params to pass to the terminate job (if using a parameterized build (see [Job Parameters](#job-parameters) section)

### Job Parameters

When calling [parameterized jobs](https://wiki.jenkins-ci.org/display/JENKINS/Parameterized+Build), you can configure which parameters to pass to the job in the config file. The values can be static (`PARAM1=foo&PARAM2=bar`) or dynamic (`PARAM1=foo&PARAM2=%(hostname)s`). The function will lookup the following information about the instance in question and allow for interpolating these variables as parameters:

- `id`: the EC2 ID of the instance (ex. 'i-xxxxxxxx')
- `public_hostname`: the public hostname (if any) assigned to the instance
- `public_ip`: the public ip (if any) assigned to the instance
- `private_hostname`: the private hostname (if any) assigned to the instance
- `private_ip`: the private ip (if any) assigned to the instance
- tags: any tags assigned to the instance (such as `Name`) can be referenced here by their key name.

For example, if my create job required two parameters, `IP` and `ENVIRONMENT`, where `IP` should be the private IP of the instance and `ENVIRONMENT` should be the value of the `environment` tag set on the instance, my config would look like:

```
[jenkins]
create_job = instance-create-job
create_job_token = 0123456789
create_job_params = IP=%(private_ip)s&ENVIRONMENT=%(environment)s
```

### Local Config

If you choose to bundle a config with the Lambda source code, any change to the config will require a new build and upload to the Lambda function. To create a new build, use the following steps (executed from this repo):

1. Create a `config.ini` file

```
cp config.ini.sample config.ini
```

2. Edit the file, applying your settings
3. Download a [release](https://github.com/nextrevision/jenkins-asg-lambda/releases) or [create a new build](#building)
4. Add your `config.ini` to the zip.

```
zip -g jenkins-asg-lambda.zip config.ini
```

5. Upload to Lambda.

### S3 Configs

The function supports retrieving configs from an S3 bucket and relies on [notification metadata](http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-as-lifecyclehook.html#cfn-as-lifecyclehook-notificationmetadata) passed in by the [ASG lifecycle hook](http://docs.aws.amazon.com/AutoScaling/latest/DeveloperGuide/lifecycle-hooks.html) to determine the bucket and config file to download. The function requires the following notification metadata to be set when downloading from S3:

```
{
  "s3_bucket": "my-asg-config-bucket",
  "s3_config_file": "config-foo.ini",
}
```

Of course, in order for the Lambda function to be able to download the config file from S3, it must have the following permissions:

```
{
  "Effect": "Allow",
  "Action": [
    "s3:GetObject",
    "s3:ListAllMyBuckets",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:s3:::my-asg-config-bucket/*"
  ]
}
```

## Building

AWS Lambda requires all python modules to be uploaded along with the function. The easiest way to build is using [Docker](https://docker.io) like so:

```
#!/bin/bash -ex
[ -f jenkins-asg-lambda.zip ] && rm jenkins-asg-lambda.zip
cd lib
docker run --rm \
  -v $(pwd):/work \
  --entrypoint /usr/local/bin/pip \
  python:2.7 install -r /work/requirements.txt --upgrade -t /work/lib
zip -r9 ../jenkins-asg-lambda.zip *
cd ../
zip -g jenkins-asg-lambda.zip JekninsJob.py setup.cfg
```

This will produce `jenkins-asg-lambda.zip` which you can upload to Lambda.

## Instead, Why Don't You

- Use SNS to call Jenkins over HTTP?

The short answer is when using authentication, you need to receive a [CSRF](https://wiki.jenkins-ci.org/display/JENKINS/Remote+access+API) token before making the call. That's a total of two requests, one to receive a token, and one to call the build API for the job while passing the token from the first call.

- Use cloud-init and user-data to make the call?

While this works on creation events, I am unaware of cloud-init being able to apply action at termination events. This Lambda function allows for calling a separate job for termination events.

- Use Javascript instead of Python?

Laziness. It is nice being able to use credstash for secret retrieval, and including that project is easier than reimplementing in Javascript.
