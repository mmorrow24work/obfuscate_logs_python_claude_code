1. Create a repo called https://github.com/mmorrow24work/obfuscate_logs_python_claude_code
2. Create README.md to capture objectives, and examples of how to use the python scripts
3. Create a python script to obfuscate IP addresses within a set of logs that are inside a zip file 

- The zip file contains 1,000's of log files 
- The logs use IP addresses in IPv4 format - e.g. 192.168.001.001 or CIDR form 192.168.1.1/24 
- The zip and log filename might include hostnames - these must also be obfuscated
- The output from the python script should be :
* i. a new zip file containing the logs after they are obfuscated - do not leak the hostname in the output zip and log filename
* ii. a text file detailing the obfuscation encode which translates the original data to the new data in case we need to reference the obfuscated IP's
* iii. a text file detailing the obfuscation decode which translates the obfuscated data to the original data in case we need to reference the obfuscated IP's

4. Create a python script to generate test log files with an input switch to define logfile size
5. Create a Code Walkthrough markup document for both python scripts that clearly explain the code for future maintenance
6. Before creating any code, enter plan mode


---

1. Create a repo
2. Create `README.md` to capture objectives and usage examples
3. Create an obfuscation script
   - Input: a zip containing thousands of log files
   - Log format: IPv4 (e.g. `192.168.1.1`) and CIDR (e.g. `192.168.1.1/24`)
   - Zip and log filenames may contain hostnames — must also be obfuscated
   - Output:
     - i. Obfuscated zip — no hostname leakage in zip or log filenames
     - ii. Encode map — original → obfuscated
     - iii. Decode map — obfuscated → original
4. Create a test log generator with `--size` input switch
5. Create a Code Walkthrough document for both scripts
6. Enter plan mode before writing any code
