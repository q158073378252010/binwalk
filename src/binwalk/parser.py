import io
import re
import os.path
import tempfile
from binwalk.compat import *
from binwalk.common import str2int

class MagicParser:
	'''
	Class for loading, parsing and creating libmagic-compatible magic files.
	
	This class is primarily used internally by the Binwalk class, and a class instance of it is available via the Binwalk.parser object.

	One useful method however, is file_from_string(), which will generate a temporary magic file from a given signature string:

		import binwalk

		bw = binwalk.Binwalk()

		# Create a temporary magic file that contains a single entry with a signature of '\\x00FOOBAR\\xFF', and append the resulting 
		# temporary file name to the list of magic files in the Binwalk class instance.
		bw.magic_files.append(bw.parser.file_from_string('\\x00FOOBAR\\xFF', display_name='My custom signature'))

		bw.scan('firmware.bin')
	
	All magic files generated by this class will be deleted when the class deconstructor is called.
	'''

	BIG_ENDIAN = 'big'
	LITTLE_ENDIAN = 'little'

	MAGIC_STRING_FORMAT = "%d\tstring\t%s\t%s\n"
	DEFAULT_DISPLAY_NAME = "Raw string signature"

	WILDCARD = 'x'

	# If libmagic returns multiple results, they are delimited with this string.	
	RESULT_SEPERATOR = "\\012- "

	def __init__(self, filter=None, smart=None):
		'''
		Class constructor.

		@filter - Instance of the MagicFilter class. May be None if the parse/parse_file methods are not used.
		@smart  - Instance of the SmartSignature class. May be None if the parse/parse_file methods are not used.

		Returns None.
		'''
		self.matches = set([])
		self.signatures = {}
		self.filter = filter
		self.smart = smart
		self.raw_fd = None
		self.signature_count = 0
		self.fd = tempfile.NamedTemporaryFile()

	def __del__(self):
		try:
			self.cleanup()
		except:
			pass

	def rm_magic_file(self):
		'''
		Cleans up the temporary magic file generated by self.parse.

		Returns None.
		'''
		try:
			self.fd.close()
		except:
			pass

	def cleanup(self):
		'''
		Cleans up any tempfiles created by the class instance.

		Returns None.
		'''
		self.rm_magic_file()

		try:
			self.raw_fd.close()
		except:
			pass

	def file_from_string(self, signature_string, offset=0, display_name=DEFAULT_DISPLAY_NAME):
		'''
		Generates a magic file from a signature string.
		This method is intended to be used once per instance.
		If invoked multiple times, any previously created magic files will be closed and deleted.

		@signature_string - The string signature to search for.
		@offset           - The offset at which the signature should occur.
		@display_name     - The text to display when the signature is found.

		Returns the name of the generated temporary magic file.
		'''
		self.raw_fd = tempfile.NamedTemporaryFile()
		self.raw_fd.write(self.MAGIC_STRING_FORMAT % (offset, signature_string, display_name))
		self.raw_fd.seek(0)
		return self.raw_fd.name

	def parse(self, file_name):
		'''
		Parses magic file(s) and contatenates them into a single temporary magic file
		while simultaneously removing filtered signatures.

		@file_name - Magic file, or list of magic files, to parse.

		Returns the name of the generated temporary magic file, which will be automatically
		deleted when the class deconstructor is called.
		'''
		if isinstance(file_name, type([])):
			files = file_name
		else:
			files = [file_name]

		for fname in files:
			if os.path.exists(fname):
				self.parse_file(fname)
			else:
				sys.stdout.write("WARNING: Magic file '%s' does not exist!\n" % fname)

		self.fd.seek(0)
		return self.fd.name

	def parse_file(self, file_name):
		'''
		Parses a magic file and appends valid signatures to the temporary magic file, as allowed
		by the existing filter rules.

		@file_name - Magic file to parse.
		
		Returns None.
		'''
		# Default to not including signature entries until we've
		# found what looks like a valid entry.
		include = False
		line_count = 0

		try:
			for line in io.FileIO(file_name).readlines():
				line_count += 1

				# Check if this is the first line of a signature entry
				entry = self._parse_line(line)

				if entry is not None:
					# If this signature is marked for inclusion, include it.
					if self.filter.filter(entry['description']) == self.filter.FILTER_INCLUDE:

						include = True	
						self.signature_count += 1

						if not has_key(self.signatures, entry['offset']):
							self.signatures[entry['offset']] = []
						
						if entry['condition'] not in self.signatures[entry['offset']]:
							self.signatures[entry['offset']].append(entry['condition'])
					else:
						include = False

				# Keep writing lines of the signature to the temporary magic file until 
				# we detect a signature that should not be included.
				if include:
					self.fd.write(line)

			self.build_signature_set()			
		except Exception as e:
			raise Exception("Error parsing magic file '%s' on line %d: %s" % (file_name, line_count, str(e)))
		
	def _parse_line(self, line):
		'''
		Parses a signature line into its four parts (offset, type, condition and description),
		looking for the first line of a given signature.

		@line - The signature line to parse.

		Returns a dictionary with the respective line parts populated if the line is the first of a signature.
		Returns a dictionary with all parts set to None if the line is not the first of a signature.
		'''
		entry = {
			'offset'	: '',
			'type'		: '',
			'condition'	: '',
			'description'	: '',
			'length'	: 0
		}

		# Quick and dirty pre-filter. We are only concerned with the first line of a
		# signature, which will always start with a number. Make sure the first byte of
		# the line is a number; if not, don't process.
		if bytes2str(line[:1]) < '0' or bytes2str(line[:1]) > '9':
			return None

		try:
			# Split the line into white-space separated parts.
			# For this to work properly, replace escaped spaces ('\ ') with '\x20'.
			# This means the same thing, but doesn't confuse split().
			line_parts = bytes2str(line).replace('\\ ', '\\x20').split()
			entry['offset'] = line_parts[0]
			entry['type'] = line_parts[1]
			# The condition line may contain escaped sequences, so be sure to decode it properly.
			entry['condition'] = string_decode(line_parts[2])
			entry['description'] = ' '.join(line_parts[3:])
		except Exception as e:
			raise Exception("%s :: %s", (str(e), line))

		# We've already verified that the first character in this line is a number, so this *shouldn't*
		# throw an exception, but let's catch it just in case...
		try:
			entry['offset'] = str2int(entry['offset'])
		except Exception as e:
			raise Exception("%s :: %s", (str(e), line))

		# If this is a string, get the length of the string
		if 'string' in entry['type'] or entry['condition'] == self.WILDCARD:
			entry['length'] = len(entry['condition'])
		# Else, we need to jump through a few more hoops...
		else:	
			# Default to little endian, unless the type field starts with 'be'. 
			# This assumes that we're running on a little endian system...
			if entry['type'].startswith('be'):
				endianess = self.BIG_ENDIAN
			else:
				endianess = self.LITTLE_ENDIAN
			
			# Try to convert the condition to an integer. This does not allow
			# for more advanced conditions for the first line of a signature, 
			# but needing that is rare.
			try:
				intval = str2int(entry['condition'].strip('L'))
			except Exception as e:
				raise Exception("Failed to evaluate condition for '%s' type: '%s', condition: '%s', error: %s" % (entry['description'], entry['type'], entry['condition'], str(e)))

			# How long is the field type?
			if entry['type'] == 'byte':
				entry['length'] = 1
			elif 'short' in entry['type']:
				entry['length'] = 2
			elif 'long' in entry['type']:
				entry['length'] = 4
			elif 'quad' in entry['type']:
				entry['length'] = 8

			# Convert the integer value to a string of the appropriate endianess
			entry['condition'] = self._to_string(intval, entry['length'], endianess)

		return entry

	def build_signature_set(self):
		'''
		Builds a list of signature tuples.

		Returns a list of tuples in the format: [(<signature offset>, [signature regex])].
		'''
		signature_set = []

		for (offset, sigs) in iterator(self.signatures):
			for sig in sigs:
				if sig == self.WILDCARD:
					sig = re.compile('.')
				else:
					sig = re.compile(re.escape(sig))

				signature_set.append(sig)

		self.signature_set = set(signature_set)

		return self.signature_set

	def find_signature_candidates(self, data, end):
		'''
		Finds candidate signatures inside of the data buffer.
		Called internally by Binwalk.single_scan.

		@data - Data to scan for candidate signatures.
		@end  - Don't look for signatures beyond this offset.

		Returns an ordered list of offsets inside of data at which candidate offsets were found.
		'''
		candidate_offsets = []
		data = bytes2str(data)

		for regex in self.signature_set:
			candidate_offsets += [match.start() for match in regex.finditer(data) if match.start() < end]

		candidate_offsets = list(set(candidate_offsets))
		candidate_offsets.sort()

		return candidate_offsets

	def _to_string(self, value, size, endianess):
		'''
		Converts an integer value into a raw string.

		@value     - The integer value to convert.
		@size      - Size, in bytes, of the integer value.
		@endianess - One of self.LITTLE_ENDIAN | self.BIG_ENDIAN.

		Returns a raw string containing value.
		'''
		data = ""

		for i in range(0, size):
			data += chr((value >> (8*i)) & 0xFF)

		if endianess != self.LITTLE_ENDIAN:
			data = data[::-1]

		return data

	def split(self, data):
		'''
		Splits multiple libmagic results in the data string into a list of separate results.

		@data - Data string returned from libmagic.

		Returns a list of result strings.
		'''
		try:
			return data.split(self.RESULT_SEPERATOR)
		except:
			return []

